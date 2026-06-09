"""
Step 1 — Empresas Selecionadas.

Extrai da Bronze as empresas elegíveis para análise financeira
e persiste na camada Silver.

Input:  layer_01_bronze.cad_cia_aberta
Output: layer_02_silver.n0_empresas_selecionadas
"""

import logging

import pandas as pd
from sqlalchemy import text

from silver.utils import SCHEMA_BRONZE, SCHEMA_SILVER, create_db_engine, table_exists

logger = logging.getLogger(__name__)

# Critérios de elegibilidade:
# - Ativas e listadas na Bolsa (B3)
# - Em fase operacional (exclui pré-operacionais)
# - Sem setores com contabilidade incomparável (bancos, seguradoras, holdings etc.)
QUERY = """
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
      AND "SETOR_ATIV" NOT LIKE '%Adm.%Imóv%'
"""


def check_prerequisites(engine) -> bool:
    """Verifica se as tabelas de entrada existem antes de executar."""
    if not table_exists(engine, SCHEMA_BRONZE, "cad_cia_aberta"):
        logger.error(
            "Tabela '%s.cad_cia_aberta' não encontrada. "
            "Rode a camada Bronze com '--dataset CAD' antes de continuar.",
            SCHEMA_BRONZE,
        )
        return False
    return True


def run():
    """Executa o Step 1: extrai empresas elegíveis e escreve na Silver."""
    engine = create_db_engine()

    if not check_prerequisites(engine):
        return

    logger.info("Extraindo empresas elegíveis de '%s.cad_cia_aberta'...", SCHEMA_BRONZE)
    with engine.connect() as conn:
        df = pd.read_sql(text(QUERY), con=conn)

    logger.info("%d empresas selecionadas.", len(df))

    logger.info("Escrevendo em '%s.n0_empresas_selecionadas'...", SCHEMA_SILVER)
    df.to_sql(
        name="n0_empresas_selecionadas",
        schema=SCHEMA_SILVER,
        con=engine,
        if_exists="replace",
        index=False,
        chunksize=10000,
        method="multi",
    )
    logger.info("Step 1 concluído com sucesso.")
