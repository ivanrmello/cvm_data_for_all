"""
Step 4 — DRE — Demonstração do Resultado do Exercício.

Aplica Golden Map para padronização de nomes de conta da DRE,
extrai hierarquia (Safe Mode V11) e persiste na camada Silver.

Input:  layer_02_silver.n0_dfp_cia_aberta + layer_02_silver.n0_empresas_selecionadas
Output: layer_02_silver.n1_dre
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
# Golden Map — padronização de nomes de conta da DRE
# ---------------------------------------------------------------------------
GOLDEN_MAP_DRE = {
    "Receita de Venda de Bens e/ou Serviços":              "RECEITA LÍQUIDA",
    "Receita Bruta":                                        "RECEITA LÍQUIDA",
    "Receita Líquida":                                      "RECEITA LÍQUIDA",
    "Custo dos Bens e/ou Serviços Vendidos":               "CUSTO DAS VENDAS",
    "Custo das Mercadorias Vendidas":                       "CUSTO DAS VENDAS",
    "Resultado Bruto":                                      "LUCRO BRUTO",
    "Lucro Bruto":                                          "LUCRO BRUTO",
    "Despesas com Vendas":                                  "DESPESAS DE VENDAS",
    "Despesas Gerais e Administrativas":                    "DESPESAS G&A",
    "Outras Receitas e Despesas Operacionais":              "OUTRAS RECEITAS/DESPESAS OPERACIONAIS",
    "Resultado Antes do Resultado Financeiro e dos Tributos": "EBIT",
    "Resultado Financeiro":                                 "RESULTADO FINANCEIRO",
    "Receitas Financeiras":                                 "RECEITAS FINANCEIRAS",
    "Despesas Financeiras":                                 "DESPESAS FINANCEIRAS",
    "Resultado Antes dos Tributos sobre o Lucro":          "RESULTADO ANTES IR/CS",
    "Imposto de Renda e Contribuição Social sobre o Lucro": "IR E CSLL",
    "Resultado Líquido das Operações Continuadas":          "LUCRO OPERAÇÕES CONTINUADAS",
    "Resultado Líquido de Operações Descontinuadas":        "RESULTADO OPERAÇÕES DESCONTINUADAS",
    "Lucro/Prejuízo Consolidado do Período":                "LUCRO LÍQUIDO",
    "Lucro/Prejuízo do Período":                            "LUCRO LÍQUIDO",
    "Atribuído a Sócios da Empresa Controladora":           "LUCRO LÍQUIDO CONTROLADORA",
    "Atribuído a Sócios Não Controladores":                 "LUCRO LÍQUIDO NÃO CONTROLADORES",
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
    df["DS_CONTA"] = df["DS_CONTA"].replace(GOLDEN_MAP_DRE)
    df["FLAG_NORMALIZACAO"] = df["DS_CONTA"] != df["DS_CONTA_REPORTADA"]
    return df


def calcular_status_dre(df: pd.DataFrame) -> pd.DataFrame:
    """
    Valida: Receita Líquida - Custo das Vendas ≈ Lucro Bruto (conta 3.01, 3.02, 3.03).
    STATUS_MATH: CONSISTENTE | INCONSISTENTE | SEM_DADOS
    """
    df = df.copy()
    df["STATUS_MATH"] = "SEM_DADOS"

    chaves = ["CNPJ_CIA", "DT_REFER", "VERSAO"]

    receita = (
        df[df["CD_CONTA"] == "3.01"].groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("RECEITA")
    )
    custo = (
        df[df["CD_CONTA"] == "3.02"].groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("CUSTO")
    )
    lucro_bruto = (
        df[df["CD_CONTA"] == "3.03"].groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("LUCRO_BRUTO")
    )

    validacao = receita.to_frame().join(custo.to_frame(), how="inner").join(lucro_bruto.to_frame(), how="inner")
    if not validacao.empty:
        validacao["STATUS_MATH"] = (
            (validacao["RECEITA"] + validacao["CUSTO"] - validacao["LUCRO_BRUTO"]).abs() < 2
        ).map({True: "CONSISTENTE", False: "INCONSISTENTE"})

        df = df.merge(validacao[["STATUS_MATH"]], left_on=chaves, right_index=True, how="left", suffixes=("_old", ""))
        if "STATUS_MATH_old" in df.columns:
            df["STATUS_MATH"] = df["STATUS_MATH"].fillna(df["STATUS_MATH_old"])
            df.drop(columns=["STATUS_MATH_old"], inplace=True)

    df["STATUS_MATH"] = df["STATUS_MATH"].fillna("SEM_DADOS")

    total = len(validacao)
    cons = (validacao.get("STATUS_MATH", pd.Series()) == "CONSISTENTE").sum() if not validacao.empty else 0
    logger.info("Validação DRE: %d/%d períodos consistentes (%.1f%%)", cons, total, 100 * cons / total if total else 0)
    return df


def run():
    """Executa o Step 4: DRE com Golden Map, hierarquia e validação."""
    engine = create_db_engine()

    if not check_prerequisites(engine):
        return

    logger.info("Lendo n0_dfp_cia_aberta (filtro DRE)...")
    query = """
        SELECT d.*, e."SETOR_ATIV"
        FROM layer_02_silver.n0_dfp_cia_aberta d
        INNER JOIN layer_02_silver.n0_empresas_selecionadas e
            ON d."CNPJ_CIA" = e."CNPJ_CIA"
        WHERE d."GRUPO_DFP_TRATADO" = 'DRE'
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), con=conn)

    if df.empty:
        logger.error("Nenhum dado de DRE encontrado. Verifique a tabela n0_dfp_cia_aberta.")
        return

    logger.info("%d linhas de DRE carregadas.", len(df))

    logger.info("Aplicando Golden Map...")
    df = aplicar_golden_map(df)

    logger.info("Extraindo níveis hierárquicos (Safe Mode V11)...")
    df = extrair_niveis_hierarquicos(df)

    logger.info("Calculando status matemático (equação DRE)...")
    df = calcular_status_dre(df)

    df["CONTA_NOME_COMPLETO"] = df["CD_CONTA"].astype(str) + " - " + df["DS_CONTA"].astype(str)
    df["FLAG_RECONSTRUCAO"] = False
    df["ST_CONTA_FIXA_REPORTADA"] = df.get("ST_CONTA_FIXA", None)

    logger.info("Padronizando para Golden Schema...")
    df_final = padronizar_schema_silver(df, "DRE")

    logger.info("Escrevendo em '%s.n1_dre'...", SCHEMA_SILVER)
    df_final.to_sql(
        name="n1_dre",
        schema=SCHEMA_SILVER,
        con=engine,
        if_exists="replace",
        index=False,
        chunksize=10000,
        method="multi",
    )
    logger.info("Step 4 concluído com sucesso.")
