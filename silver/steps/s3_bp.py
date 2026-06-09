"""
Step 3 — Balanço Patrimonial (BP) — Hierarquia e Validação.

Aplica o Safe Mode V11 para reconstrução dos níveis hierárquicos
do BP, normaliza nomes via Golden Map, calcula status matemático
e persiste na camada Silver.

Input:  layer_02_silver.n0_dfp_cia_aberta + layer_02_silver.n0_empresas_selecionadas
Output: layer_02_silver.n1_bp
"""

import logging

import pandas as pd
from sqlalchemy import text

from silver.utils import (
    SCHEMA_BRONZE,
    SCHEMA_SILVER,
    create_db_engine,
    padronizar_schema_silver,
    table_exists,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Golden Map — padronização de nomes de conta do BP
# ---------------------------------------------------------------------------
GOLDEN_MAP_BP = {
    # Ativo
    "Ativo Total":                             "ATIVO TOTAL",
    "Ativo Circulante":                        "ATIVO CIRCULANTE",
    "Ativo Não Circulante":                    "ATIVO NÃO CIRCULANTE",
    "Realizável a Longo Prazo":                "REALIZÁVEL A LONGO PRAZO",
    "Investimentos":                           "INVESTIMENTOS",
    "Imobilizado":                             "IMOBILIZADO",
    "Intangível":                              "INTANGÍVEL",
    # Passivo
    "Passivo Total":                           "PASSIVO TOTAL",
    "Passivo Circulante":                      "PASSIVO CIRCULANTE",
    "Passivo Não Circulante":                  "PASSIVO NÃO CIRCULANTE",
    "Patrimônio Líquido Consolidado":          "PATRIMÔNIO LÍQUIDO",
    "Patrimônio Líquido":                      "PATRIMÔNIO LÍQUIDO",
    "Capital Social Realizado":                "CAPITAL SOCIAL REALIZADO",
    "Reservas de Capital":                     "RESERVAS DE CAPITAL",
    "Reservas de Lucros":                      "RESERVAS DE LUCROS",
    "Lucros/Prejuízos Acumulados":             "LUCROS/PREJUÍZOS ACUMULADOS",
    "Outros Resultados Abrangentes":           "OUTROS RESULTADOS ABRANGENTES",
}

# Contas de nível 1 esperadas para o BP (prefixos padrão CVM)
CONTAS_NIVEL1_BP = {"1", "2"}


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
    """
    Safe Mode V11: extrai até 5 níveis hierárquicos a partir do CD_CONTA.
    Exemplo: '1.01.02.03' → DS_NIVEL_1='Ativo Total', DS_NIVEL_2=..., etc.
    """
    df = df.copy()

    # Tabela de referência: conta → descrição (para lookup de nível)
    lookup = df[["CD_CONTA", "DS_CONTA"]].drop_duplicates().set_index("CD_CONTA")["DS_CONTA"].to_dict()

    def get_nivel(conta_str, nivel):
        partes = str(conta_str).split(".")
        if len(partes) < nivel:
            return None
        prefixo = ".".join(partes[:nivel])
        return lookup.get(prefixo)

    for n in range(1, 6):
        df[f"DS_NIVEL_{n}"] = df["CD_CONTA"].apply(lambda c: get_nivel(c, n))

    return df


def aplicar_golden_map(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza DS_CONTA via Golden Map e registra flag de normalização."""
    df = df.copy()
    df["DS_CONTA_REPORTADA"] = df["DS_CONTA"]
    df["DS_CONTA"] = df["DS_CONTA"].replace(GOLDEN_MAP_BP)
    df["FLAG_NORMALIZACAO"] = df["DS_CONTA"] != df["DS_CONTA_REPORTADA"]
    return df


def reconstruir_hierarquia(df: pd.DataFrame) -> pd.DataFrame:
    """
    Safe Mode V11: para contas IS_LEAF, recalcula o valor das contas pai
    somando os filhos diretos. Registra FLAG_RECONSTRUCAO quando recalculado.
    """
    df = df.copy()
    df["FLAG_RECONSTRUCAO"] = False

    chaves_grupo = ["CNPJ_CIA", "DT_REFER", "VERSAO", "GRUPO_DFP_TRATADO"]

    contas_pai = df[~df["IS_LEAF"]]["CD_CONTA"].unique()

    for conta_pai in contas_pai:
        nivel = len(conta_pai.split("."))
        filhos_mask = (
            df["CD_CONTA"].str.startswith(conta_pai + ".")
            & (df["CD_CONTA"].str.count(r"\.") == nivel)
            & df["IS_LEAF"]
        )
        if not filhos_mask.any():
            continue

        soma_filhos = (
            df[filhos_mask]
            .groupby(chaves_grupo)["VL_CONTA_TRATADO"]
            .sum()
            .reset_index()
            .rename(columns={"VL_CONTA_TRATADO": "VL_RECONSTRUIDO"})
        )

        pai_mask = df["CD_CONTA"] == conta_pai
        df_pai = df[pai_mask].merge(soma_filhos, on=chaves_grupo, how="left")

        diferenca = (df_pai["VL_CONTA_TRATADO"] - df_pai["VL_RECONSTRUIDO"]).abs()
        reconstruir = diferenca > 1

        if reconstruir.any():
            idx = df[pai_mask].index
            df.loc[idx, "VL_CONTA_TRATADO"] = df_pai["VL_RECONSTRUIDO"].values
            df.loc[idx, "FLAG_RECONSTRUCAO"] = True

    return df


def calcular_status_balanco(df: pd.DataFrame) -> pd.DataFrame:
    """
    Valida a equação fundamental: Ativo Total = Passivo Total + PL.
    STATUS_MATH: BALANCEADO | DESEQUILIBRADO | SEM_DADOS
    """
    df = df.copy()
    df["STATUS_MATH"] = "SEM_DADOS"

    chaves = ["CNPJ_CIA", "DT_REFER", "VERSAO"]

    ativo = (
        df[df["CD_CONTA"].str.startswith("1.") & (df["CD_CONTA"].str.count(r"\.") == 1)]
        .groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("ATIVO")
    )
    passivo = (
        df[df["CD_CONTA"].str.startswith("2.") & (df["CD_CONTA"].str.count(r"\.") == 1)]
        .groupby(chaves)["VL_CONTA_TRATADO"].sum().rename("PASSIVO")
    )

    equacao = ativo.to_frame().join(passivo.to_frame(), how="inner")
    equacao["STATUS_MATH"] = (
        (equacao["ATIVO"] - equacao["PASSIVO"]).abs() < 2
    ).map({True: "BALANCEADO", False: "DESEQUILIBRADO"})

    df = df.merge(equacao[["STATUS_MATH"]], left_on=chaves, right_index=True, how="left", suffixes=("_old", ""))
    if "STATUS_MATH_old" in df.columns:
        df["STATUS_MATH"] = df["STATUS_MATH"].fillna(df["STATUS_MATH_old"])
        df.drop(columns=["STATUS_MATH_old"], inplace=True)
    df["STATUS_MATH"] = df["STATUS_MATH"].fillna("SEM_DADOS")

    total = len(equacao)
    bal = (equacao["STATUS_MATH"] == "BALANCEADO").sum()
    logger.info("Validação BP: %d/%d períodos balanceados (%.1f%%)", bal, total, 100 * bal / total if total else 0)
    return df


def run():
    """Executa o Step 3: BP com hierarquia Safe Mode V11 e validação matemática."""
    engine = create_db_engine()

    if not check_prerequisites(engine):
        return

    logger.info("Lendo n0_dfp_cia_aberta (filtro BP)...")
    query = """
        SELECT d.*, e."SETOR_ATIV"
        FROM layer_02_silver.n0_dfp_cia_aberta d
        INNER JOIN layer_02_silver.n0_empresas_selecionadas e
            ON d."CNPJ_CIA" = e."CNPJ_CIA"
        WHERE d."GRUPO_DFP_TRATADO" = 'BP'
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), con=conn)

    if df.empty:
        logger.error("Nenhum dado de BP encontrado. Verifique a tabela n0_dfp_cia_aberta.")
        return

    logger.info("%d linhas de BP carregadas.", len(df))

    logger.info("Aplicando Golden Map...")
    df = aplicar_golden_map(df)

    logger.info("Extraindo níveis hierárquicos (Safe Mode V11)...")
    df = extrair_niveis_hierarquicos(df)

    logger.info("Reconstruindo hierarquia...")
    df = reconstruir_hierarquia(df)

    logger.info("Calculando status matemático (equação patrimonial)...")
    df = calcular_status_balanco(df)

    df["CONTA_NOME_COMPLETO"] = df["CD_CONTA"].astype(str) + " - " + df["DS_CONTA"].astype(str)

    logger.info("Padronizando para Golden Schema...")
    df_final = padronizar_schema_silver(df, "BP")

    logger.info("Escrevendo em '%s.n1_bp'...", SCHEMA_SILVER)
    df_final.to_sql(
        name="n1_bp",
        schema=SCHEMA_SILVER,
        con=engine,
        if_exists="replace",
        index=False,
        chunksize=10000,
        method="multi",
    )
    logger.info("Step 3 concluído com sucesso.")
