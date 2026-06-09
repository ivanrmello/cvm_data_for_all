"""
Step 5 — DFC — Demonstração dos Fluxos de Caixa.

Aplica Golden Map, extrai hierarquia (Safe Mode V11), valida
a equação de variação de caixa e persiste na camada Silver.

Input:  layer_02_silver.n0_dfp_cia_aberta + layer_02_silver.n0_empresas_selecionadas
Output: layer_02_silver.n1_dfc
"""

import logging

import pandas as pd
from sqlalchemy import text

from silver.utils import (
    SCHEMA_SILVER,
    create_db_engine,
    padronizar_schema_silver,
    table_exists,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Golden Map — padronização de nomes de conta da DFC (Método Indireto)
# ---------------------------------------------------------------------------
GOLDEN_MAP_DFC = {
    "Caixa Líquido Atividades Operacionais":               "FCO - FLUXO DE CAIXA OPERACIONAL",
    "Caixa Gerado nas Operações":                          "FCO - FLUXO DE CAIXA OPERACIONAL",
    "Caixa Líquido Atividades de Investimento":            "FCI - FLUXO DE CAIXA DE INVESTIMENTO",
    "Caixa Líquido Atividades de Financiamento":           "FCF - FLUXO DE CAIXA DE FINANCIAMENTO",
    "Variação Cambial sobre Caixa e Equivalentes":         "VARIAÇÃO CAMBIAL SOBRE CAIXA",
    "Aumento (Redução) de Caixa e Equivalentes":           "VARIAÇÃO LÍQUIDA DE CAIXA",
    "Variação Líquida de Caixa e Equivalentes":            "VARIAÇÃO LÍQUIDA DE CAIXA",
    "Caixa e Equivalentes Caixa Início do Período":        "SALDO INICIAL DE CAIXA",
    "Caixa e Equivalentes Caixa Final do Período":         "SALDO FINAL DE CAIXA",
}


def check_prerequisites(engine) -> bool:
    """Verifica se as tabelas de entrada existem antes de executar."""
    for tabela in ["n0_dfp_cia_aberta", "n0_empresas_selecionadas"]:
        if not table_exists(engine, SCHEMA_SILVER, tabela):
            logger.error(
                "Tabela '%s.%s' não encontrada. "
                "Rode '--step consolidar' antes de continuar.",
                SCHEMA_SILVER,
                tabela,
            )
            return False
    return True


def extrair_niveis_hierarquicos(df: pd.DataFrame) -> pd.DataFrame:
    """Safe Mode V11: extrai até 5 níveis hierárquicos a partir do CD_CONTA."""
    df = df.copy()
    lookup = df[["CD_CONTA", "DS_CONTA"]].drop_duplicates().set_index("CD_CONTA")["DS_CONTA"].to_dict()

    def get_nivel(conta_str, nivel):
        partes = str(conta_str).split(".")
        if len(partes) < nivel:
            return None
        prefixo = ".".join(partes[:nivel])
        return lookup.get(prefixo)

    for n in range(1, 6):
        df[f"DS_NIVEL_{n}"] = df["CD_CONTA"].apply(lambda c, n=n: get_nivel(c, n))

    return df


def aplicar_golden_map(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza DS_CONTA via Golden Map e registra flag de normalização."""
    df = df.copy()
    df["DS_CONTA_REPORTADA"] = df["DS_CONTA"]
    df["DS_CONTA"] = df["DS_CONTA"].replace(GOLDEN_MAP_DFC)
    df["FLAG_NORMALIZACAO"] = df["DS_CONTA"] != df["DS_CONTA_REPORTADA"]
    return df


def calcular_status_dfc(df: pd.DataFrame) -> pd.DataFrame:
    """
    Valida: Saldo Final = Saldo Inicial + FCO + FCI + FCF (+ variação cambial).
    Conta 6.05 (variação líquida) = 6.01 + 6.02 + 6.03.
    STATUS_MATH: CONSISTENTE | INCONSISTENTE | SEM_DADOS
    """
    df = df.copy()
    df["STATUS_MATH"] = "SEM_DADOS"

    chaves = ["CNPJ_CIA", "DT_REFER", "VERSAO"]

    fco = df[df["CD_CONTA"] == "6.01"].groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("FCO")
    fci = df[df["CD_CONTA"] == "6.02"].groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("FCI")
    fcf = df[df["CD_CONTA"] == "6.03"].groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("FCF")
    variacao = df[df["CD_CONTA"] == "6.05"].groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("VARIACAO")

    validacao = (
        fco.to_frame()
        .join(fci.to_frame(), how="inner")
        .join(fcf.to_frame(), how="inner")
        .join(variacao.to_frame(), how="inner")
    )

    if not validacao.empty:
        validacao["STATUS_MATH"] = (
            (validacao["FCO"] + validacao["FCI"] + validacao["FCF"] - validacao["VARIACAO"]).abs() < 2
        ).map({True: "CONSISTENTE", False: "INCONSISTENTE"})

        df = df.merge(validacao[["STATUS_MATH"]], left_on=chaves, right_index=True, how="left", suffixes=("_old", ""))
        if "STATUS_MATH_old" in df.columns:
            df["STATUS_MATH"] = df["STATUS_MATH"].fillna(df["STATUS_MATH_old"])
            df.drop(columns=["STATUS_MATH_old"], inplace=True)

    df["STATUS_MATH"] = df["STATUS_MATH"].fillna("SEM_DADOS")

    total = len(validacao)
    cons = (validacao.get("STATUS_MATH", pd.Series()) == "CONSISTENTE").sum() if not validacao.empty else 0
    logger.info("Validação DFC: %d/%d períodos consistentes (%.1f%%)", cons, total, 100 * cons / total if total else 0)
    return df


def run():
    """Executa o Step 5: DFC com Golden Map, hierarquia e validação."""
    engine = create_db_engine()

    if not check_prerequisites(engine):
        return

    logger.info("Lendo n0_dfp_cia_aberta (filtro DFC)...")
    query = """
        SELECT d.*, e."SETOR_ATIV"
        FROM layer_02_silver.n0_dfp_cia_aberta d
        INNER JOIN layer_02_silver.n0_empresas_selecionadas e
            ON d."CNPJ_CIA" = e."CNPJ_CIA"
        WHERE d."GRUPO_DFP_TRATADO" = 'DFC'
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), con=conn)

    if df.empty:
        logger.error("Nenhum dado de DFC encontrado. Verifique a tabela n0_dfp_cia_aberta.")
        return

    logger.info("%d linhas de DFC carregadas.", len(df))

    logger.info("Aplicando Golden Map...")
    df = aplicar_golden_map(df)

    logger.info("Extraindo níveis hierárquicos (Safe Mode V11)...")
    df = extrair_niveis_hierarquicos(df)

    logger.info("Calculando status matemático (equação DFC)...")
    df = calcular_status_dfc(df)

    df["CONTA_NOME_COMPLETO"] = df["CD_CONTA"].astype(str) + " - " + df["DS_CONTA"].astype(str)
    df["FLAG_RECONSTRUCAO"] = False
    df["ST_CONTA_FIXA_REPORTADA"] = df.get("ST_CONTA_FIXA", None)

    logger.info("Padronizando para Golden Schema...")
    df_final = padronizar_schema_silver(df, "DFC")

    logger.info("Escrevendo em '%s.n1_dfc'...", SCHEMA_SILVER)
    df_final.to_sql(
        name="n1_dfc",
        schema=SCHEMA_SILVER,
        con=engine,
        if_exists="replace",
        index=False,
        chunksize=10000,
        method="multi",
    )
    logger.info("Step 5 concluído com sucesso.")
