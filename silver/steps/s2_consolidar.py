"""
Step 2 — Consolidação das DFPs na Camada Silver.

Extrai os 7 tipos de demonstrativo da Bronze, normaliza escala
(MIL → UNIDADE), deduplica versões, identifica contas folha (IS_LEAF)
e consolida em uma única tabela Silver.

Input:  layer_01_bronze.dfp_cia_aberta_bpa/bpp/dre/dra/dmpl/dfc_mi/dva (7 tabelas)
Output: layer_02_silver.n0_dfp_cia_aberta
"""

import logging

import pandas as pd
from sqlalchemy import text

from silver.utils import SCHEMA_BRONZE, SCHEMA_SILVER, create_db_engine, table_exists

logger = logging.getLogger(__name__)

TABELAS_DFP = [
    "dfp_cia_aberta_bpa_con",
    "dfp_cia_aberta_bpp_con",
    "dfp_cia_aberta_dre_con",
    "dfp_cia_aberta_dra_con",
    "dfp_cia_aberta_dmpl_con",
    "dfp_cia_aberta_dfc_mi_con",
    "dfp_cia_aberta_dva_con",
]

QUERY_AGNOSTICA = '''
WITH base_filtrada AS (
    SELECT *
    FROM layer_01_bronze.cad_cia_aberta
    WHERE "SIT" = 'ATIVO'
      AND "TP_MERC" = 'BOLSA'
      AND "SIT_EMISSOR" = 'FASE OPERACIONAL'
      AND "SETOR_ATIV" NOT LIKE '%Emp. Adm. Part%'
      AND "SETOR_ATIV" NOT LIKE '%Banc%'
      AND "SETOR_ATIV" NOT LIKE '%Segurad%'
      AND "SETOR_ATIV" NOT LIKE '%Financeira%'
      AND "SETOR_ATIV" NOT LIKE '%Securitiz%'
),
dados_financeiros AS (
    SELECT
        t1."CNPJ_CIA",
        t1."DT_REFER",
        t1."DT_REFER"::date as "DT_REFER_TRATADO",
        EXTRACT(year from t1."DT_REFER"::date) as "DT_REFER_ANO",
        t1."VERSAO",
        t1."DENOM_CIA",
        t1."CD_CVM",
        t1."GRUPO_DFP",
        t1."MOEDA",
        t1."ESCALA_MOEDA",
        t1."ORDEM_EXERC",
        t1."DT_FIM_EXERC",
        t1."DT_FIM_EXERC"::date as "DT_FIM_EXERC_TRATADO",
        EXTRACT(year from t1."DT_FIM_EXERC"::date) as "DT_FIM_EXERC_ANO",
        t1."CD_CONTA",
        LENGTH(REPLACE(t1."CD_CONTA", '.','')) as "CD_CONTA_QTD_DIGITOS",
        t1."DS_CONTA",
        t1."VL_CONTA",
        case
            when t1."CD_CONTA" LIKE '3.99%'
                then t1."VL_CONTA"::numeric
            when TRIM(UPPER(t1."ESCALA_MOEDA")) like '%MIL%'
                then TRUNC(t1."VL_CONTA"::numeric) * 1000
            else t1."VL_CONTA"::numeric
        end as "VL_CONTA_TRATADO",
        t1."ST_CONTA_FIXA",
        DENSE_RANK() OVER (
            PARTITION BY t1."CNPJ_CIA", t1."DT_REFER"
            ORDER BY t1."VERSAO" DESC
        ) as rn
    FROM layer_01_bronze.{tabela_dfp} as t1
    WHERE t1."ORDEM_EXERC" = 'ÚLTIMO'
      {filtros_adicionais}
)
SELECT
    d."CNPJ_CIA", d."DT_REFER", d."DT_REFER_TRATADO", d."DT_REFER_ANO",
    d."VERSAO", d."DENOM_CIA", d."CD_CVM", d."GRUPO_DFP",
    case
        when UPPER(TRIM(d."GRUPO_DFP")) like '%BALANÇO PATRIMONIAL%'        then 'BP'
        when UPPER(TRIM(d."GRUPO_DFP")) like '%MUTAÇÕES DO PATRIMÔNIO%'     then 'DMPL'
        when UPPER(TRIM(d."GRUPO_DFP")) like '%RESULTADO ABRANGENTE%'       then 'DRA'
        when UPPER(TRIM(d."GRUPO_DFP")) like '%VALOR ADICIONADO%'           then 'DVA'
        when UPPER(TRIM(d."GRUPO_DFP")) like '%FLUXO DE CAIXA%'             then 'DFC'
        when UPPER(TRIM(d."GRUPO_DFP")) like '%DEMONSTRAÇÃO DO RESULTADO%'  then 'DRE'
        else 'VALIDAR'
    end as "GRUPO_DFP_TRATADO",
    d."MOEDA", d."ESCALA_MOEDA", d."ORDEM_EXERC",
    d."DT_FIM_EXERC", d."DT_FIM_EXERC_TRATADO", d."DT_FIM_EXERC_ANO",
    d."CD_CONTA", d."CD_CONTA_QTD_DIGITOS", d."DS_CONTA",
    d."VL_CONTA", d."VL_CONTA_TRATADO", d."ST_CONTA_FIXA"
FROM dados_financeiros d
INNER JOIN base_filtrada b ON d."CNPJ_CIA" = b."CNPJ_CIA"
WHERE d.rn = 1
  AND d."VL_CONTA_TRATADO" != 0
ORDER BY d."CNPJ_CIA", d."DT_REFER" ASC, d."CD_CONTA" ASC;
'''


def check_prerequisites(engine) -> bool:
    """Verifica se as tabelas de entrada existem antes de executar."""
    if not table_exists(engine, SCHEMA_SILVER, "n0_empresas_selecionadas"):
        logger.error(
            "Tabela '%s.n0_empresas_selecionadas' não encontrada. "
            "Rode '--step empresas' antes de continuar.",
            SCHEMA_SILVER,
        )
        return False

    ausentes = [t for t in TABELAS_DFP if not table_exists(engine, SCHEMA_BRONZE, t)]
    if ausentes:
        logger.error(
            "As seguintes tabelas Bronze não foram encontradas: %s. "
            "Rode a camada Bronze com '--dataset DFP' antes de continuar.",
            ausentes,
        )
        return False

    return True


def processar_leafs_pandas_rapido(df_input: pd.DataFrame) -> pd.DataFrame:
    """
    Identifica contas Leaf (Folhas) usando Teoria dos Conjuntos.
    Uma conta é IS_LEAF se nenhuma outra conta no mesmo contexto
    empresa/período a tem como prefixo (ancestral).
    """
    df = df_input.copy()

    if "VL_CONTA_TRATADO" not in df.columns:
        df["VL_CONTA_TRATADO"] = pd.to_numeric(df["VL_CONTA"], errors="coerce").fillna(0)

    def get_all_ancestors(s):
        s = str(s)
        if "." not in s:
            return []
        parts = s.split(".")
        ancestors = []
        current = parts[0]
        ancestors.append(current)
        for part in parts[1:-1]:
            current += "." + part
            ancestors.append(current)
        return ancestors

    df_ativos = df[df["VL_CONTA_TRATADO"] != 0].copy()
    df_ativos["ANCESTRAIS"] = df_ativos["CD_CONTA"].apply(get_all_ancestors)
    df_ancestrais_flat = df_ativos.explode("ANCESTRAIS").dropna(subset=["ANCESTRAIS"])

    conjunto_pais = set(zip(
        df_ancestrais_flat["CNPJ_CIA"],
        df_ancestrais_flat["DT_REFER"],
        df_ancestrais_flat["VERSAO"],
        df_ancestrais_flat["ANCESTRAIS"],
    ))

    identidade_linha = zip(
        df["CNPJ_CIA"],
        df["DT_REFER"],
        df["VERSAO"],
        df["CD_CONTA"].astype(str),
    )
    df["IS_LEAF"] = [ident not in conjunto_pais for ident in identidade_linha]
    return df


def run():
    """Executa o Step 2: consolida os 7 demonstrativos DFP na Silver."""
    engine = create_db_engine()

    if not check_prerequisites(engine):
        return

    dfs = []
    with engine.connect() as conn:
        for tabela in TABELAS_DFP:
            logger.info("Processando tabela Bronze: %s...", tabela)
            filtros_extras = ""
            if "dmpl" in tabela.lower():
                filtros_extras = "AND t1.\"COLUNA_DF\" ILIKE 'Patrim%nio%L%quido%Consolidado'"
            sql = QUERY_AGNOSTICA.format(tabela_dfp=tabela, filtros_adicionais=filtros_extras)
            df_temp = pd.read_sql(text(sql), con=conn)
            if not df_temp.empty:
                df_temp["_origem_tabela"] = f"{SCHEMA_BRONZE}.{tabela}"
                dfs.append(df_temp)
            else:
                logger.warning("Tabela %s não retornou dados.", tabela)

    if not dfs:
        logger.error("Nenhum dado coletado. Abortando.")
        return

    logger.info("Concatenando %d DataFrames...", len(dfs))
    df_consolidado = pd.concat(dfs, ignore_index=True)

    logger.info("Aplicando regra IS_LEAF...")
    df_final = processar_leafs_pandas_rapido(df_consolidado)
    logger.info("IS_LEAF aplicado. Total de folhas: %d", df_final["IS_LEAF"].sum())

    logger.info("Escrevendo em '%s.n0_dfp_cia_aberta'...", SCHEMA_SILVER)
    df_final.to_sql(
        name="n0_dfp_cia_aberta",
        schema=SCHEMA_SILVER,
        con=engine,
        if_exists="replace",
        index=False,
        chunksize=10000,
        method="multi",
    )
    logger.info("Step 2 concluído com sucesso.")
