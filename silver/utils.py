"""
Utilitários compartilhados da camada Silver.

Centraliza: conexão com o banco, constantes do Golden Schema
e a função de padronização de colunas usada por todos os steps.
"""

import logging
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from urllib.parse import quote_plus

load_dotenv()

logger = logging.getLogger(__name__)

SCHEMA_BRONZE = "layer_01_bronze"
SCHEMA_SILVER = "layer_02_silver"

GOLDEN_SCHEMA = [
    "CNPJ_CIA", "SETOR_ATIV", "DT_REFER", "DT_REFER_TRATADO", "DT_REFER_ANO",
    "VERSAO", "DENOM_CIA", "CD_CVM", "GRUPO_DFP_TRATADO",
    "DT_FIM_EXERC_TRATADO", "DT_FIM_EXERC_ANO",
    "CD_CONTA", "CD_CONTA_QTD_DIGITOS",
    "DS_CONTA", "DS_CONTA_REPORTADA",
    "FLAG_NORMALIZACAO", "FLAG_RECONSTRUCAO", "STATUS_MATH",
    "CONTA_NOME_COMPLETO", "VL_CONTA_TRATADO", "ST_CONTA_FIXA", "ST_CONTA_FIXA_REPORTADA",
    "IS_LEAF", "DS_NIVEL_1", "DS_NIVEL_2", "DS_NIVEL_3", "DS_NIVEL_4", "DS_NIVEL_5",
    "_origem_tabela",
]


def create_db_engine():
    """Cria e retorna a engine de conexão com o PostgreSQL."""
    user = quote_plus(os.getenv("DB_USER"))
    password = quote_plus(os.getenv("DB_PASS"))
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    dbname = os.getenv("DB_NAME")
    return create_engine(f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}")


def ensure_silver_schema(engine) -> None:
    """Cria o schema layer_02_silver se ainda não existir."""
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA_SILVER}"))
    logger.info("Schema '%s' verificado/criado.", SCHEMA_SILVER)


def table_exists(engine, schema: str, table: str) -> bool:
    """Retorna True se a tabela existir no schema indicado."""
    return inspect(engine).has_table(table, schema=schema)


def padronizar_schema_silver(df, demonstrativo: str):
    """
    Garante que o DataFrame tenha exatamente as 29 colunas do Golden Schema,
    independente do demonstrativo (BP, DRE ou DFC).
    """
    if "STATUS_DFC" in df.columns:
        df.rename(columns={"STATUS_DFC": "STATUS_MATH"}, inplace=True)
    if "STATUS_BALANCO" in df.columns:
        df.rename(columns={"STATUS_BALANCO": "STATUS_MATH"}, inplace=True)
    if "STATUS_MATH" not in df.columns:
        df["STATUS_MATH"] = "NAO_APLICAVEL"

    cols_qualidade = ["DS_CONTA_REPORTADA", "ST_CONTA_FIXA_REPORTADA", "FLAG_NORMALIZACAO", "FLAG_RECONSTRUCAO"]
    for col in cols_qualidade:
        if col not in df.columns:
            df[col] = False if "FLAG" in col else None

    if "CONTA_NOME_COMPLETO" not in df.columns:
        df["CONTA_NOME_COMPLETO"] = df["CD_CONTA"].astype(str) + " - " + df["DS_CONTA"].astype(str)

    for col in GOLDEN_SCHEMA:
        if col not in df.columns:
            df[col] = None

    return df[GOLDEN_SCHEMA]
