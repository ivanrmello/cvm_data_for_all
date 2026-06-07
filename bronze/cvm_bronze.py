"""
Pipeline de ingestão de dados brutos da CVM (Camada Bronze).

Baixa arquivos do portal de dados abertos da CVM e carrega no PostgreSQL
sem transformações — preservando os dados exatamente como publicados.

Uso:
    python cvm_bronze.py --dataset DFP
    python cvm_bronze.py --dataset ITR --start-year 2020
    python cvm_bronze.py --dataset CAD
"""

import argparse
import datetime
import io
import logging
import os
import zipfile

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text
from urllib.parse import quote_plus

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

REQUIRED_ENV_VARS = ["DB_USER", "DB_PASS", "DB_HOST", "DB_PORT", "DB_NAME"]


def _validate_env():
    """Verifica se todas as variáveis de ambiente necessárias estão definidas."""
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        raise EnvironmentError(
            f"Variáveis de ambiente ausentes no .env: {missing}\n"
            "Copie o arquivo .env.example e preencha com suas credenciais."
        )


class CVMLakeBuilder:
    """
    Orquestra o download e carga dos dados brutos da CVM no PostgreSQL.

    Suporta: FRE, DFP, ITR (arquivos históricos por ano) e CAD (snapshot atual).
    Todos os dados são carregados sem transformação (Camada Bronze / layer_01_bronze).
    """

    DATASET_CONFIG = {
        "FRE": {
            "base_url": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/",
            "file_prefix": "fre_cia_aberta",
            "is_zip": True,
            "is_historical": True,
        },
        "DFP": {
            "base_url": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/",
            "file_prefix": "dfp_cia_aberta",
            "is_zip": True,
            "is_historical": True,
        },
        "ITR": {
            "base_url": "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/",
            "file_prefix": "itr_cia_aberta",
            "is_zip": True,
            "is_historical": True,
        },
        "CAD": {
            "base_url": "https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/",
            "file_prefix": "cad_cia_aberta",
            "is_zip": False,
            "is_historical": False,
        },
    }

    SCHEMA = "layer_01_bronze"

    def __init__(self, dataset_type: str):
        self.dataset_type = dataset_type.upper()

        if self.dataset_type not in self.DATASET_CONFIG:
            raise ValueError(
                f"Dataset '{dataset_type}' não configurado. "
                f"Opções válidas: {list(self.DATASET_CONFIG.keys())}"
            )

        self.config = self.DATASET_CONFIG[self.dataset_type]
        self.log_table = f"{self.dataset_type.lower()}_raw_logs_by_timestamp"
        self.engine = self._create_db_engine()
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }

    def _create_db_engine(self):
        user = quote_plus(os.getenv("DB_USER"))
        password = quote_plus(os.getenv("DB_PASS"))
        host = os.getenv("DB_HOST")
        port = os.getenv("DB_PORT")
        dbname = os.getenv("DB_NAME")
        return create_engine(
            f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"
        )

    # ------------------------------------------------------------------
    # Infraestrutura de banco
    # ------------------------------------------------------------------

    def setup_database(self):
        """Cria o schema e a tabela de logs se ainda não existirem."""
        create_log_query = f"""
        CREATE TABLE IF NOT EXISTS {self.SCHEMA}.{self.log_table} (
            log_id              SERIAL PRIMARY KEY,
            data_execucao       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            nivel_log           VARCHAR(50),
            ano_referencia      INT,
            arquivo_origem      VARCHAR(200),
            tabela_destino      VARCHAR(200),
            mensagem            TEXT,
            schema_drift_detectado BOOLEAN DEFAULT FALSE
        );
        """
        with self.engine.connect() as conn:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {self.SCHEMA};"))
            conn.execute(text(create_log_query))
            conn.commit()
        logger.info("Setup: tabela de logs '%s' verificada.", self.log_table)

    def _log(self, nivel: str, ano: int, arquivo: str, tabela: str, msg: str, drift: bool = False):
        """Registra um evento no banco e no logger local."""
        ano_label = str(ano) if ano > 0 else "ATUAL"
        log_msg = f"[{self.dataset_type}][{ano_label}] {msg}"

        level_map = {
            "SUCCESS": logging.INFO,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "FATAL": logging.CRITICAL,
        }
        logger.log(level_map.get(nivel, logging.INFO), log_msg)

        query = text(f"""
            INSERT INTO {self.SCHEMA}.{self.log_table}
            (nivel_log, ano_referencia, arquivo_origem, tabela_destino, mensagem, schema_drift_detectado, data_execucao)
            VALUES (:niv, :ano, :arq, :tab, :msg, :drift, NOW())
        """)
        try:
            with self.engine.connect() as conn:
                conn.execute(query, {"niv": nivel, "ano": ano, "arq": arquivo, "tab": tabela, "msg": msg, "drift": drift})
                conn.commit()
        except Exception as e:
            logger.error("Falha ao gravar log no banco: %s", e)

    # ------------------------------------------------------------------
    # Verificações
    # ------------------------------------------------------------------

    def _check_already_processed(self, filename: str) -> bool:
        """Retorna True se o arquivo já foi carregado com sucesso anteriormente."""
        query = text(f"""
            SELECT 1 FROM {self.SCHEMA}.{self.log_table}
            WHERE arquivo_origem = :arq
              AND nivel_log = 'SUCCESS'
              AND mensagem LIKE 'Processamento do arquivo finalizado%'
            LIMIT 1
        """)
        with self.engine.connect() as conn:
            return conn.execute(query, {"arq": filename}).fetchone() is not None

    def _check_schema_drift(self, table_name: str, df_new: pd.DataFrame) -> tuple[bool, str]:
        """Detecta colunas novas em relação ao schema já existente no banco."""
        inspector = inspect(self.engine)
        if not inspector.has_table(table_name, schema=self.SCHEMA):
            return False, "Nova tabela — sem drift."

        cols_db = {col["name"] for col in inspector.get_columns(table_name, schema=self.SCHEMA)}
        new_cols = set(df_new.columns) - cols_db

        if new_cols:
            return True, f"Schema drift: novas colunas detectadas: {new_cols}"
        return False, "Schema OK."

    # ------------------------------------------------------------------
    # Processamento
    # ------------------------------------------------------------------

    def _process_csv(self, csv_name: str, file_content, year: int, origin_file_name: str):
        """Lê um CSV, adiciona metadados e carrega na tabela correspondente."""
        table_name = "unknown"
        try:
            clean_name = csv_name.lower().replace(".csv", "")
            if year > 0:
                clean_name = clean_name.replace(f"_{year}", "")

            prefix = self.config["file_prefix"].split("_")[0]
            table_name = clean_name if clean_name.startswith(prefix) else f"{prefix}_{clean_name}"

            df = pd.read_csv(
                file_content,
                sep=";",
                encoding="windows-1252",
                on_bad_lines="skip",
                low_memory=False,
            )

            df["metadata_data_carga"] = datetime.datetime.now()
            df["metadata_arquivo_origem"] = origin_file_name
            df = df.astype(str)  # Bronze: tudo como texto, sem interpretação de tipos

            is_drift, drift_msg = self._check_schema_drift(table_name, df)
            if is_drift:
                self._log("WARNING", year, csv_name, table_name, drift_msg, drift=True)

            df.to_sql(
                table_name,
                self.engine,
                schema=self.SCHEMA,
                if_exists="append",
                index=False,
                method="multi",
                chunksize=1000,
            )

            self._log("INFO", year, csv_name, table_name, f"Carga OK: {len(df)} linhas.")

        except Exception as e:
            self._log("ERROR", year, csv_name, table_name, f"Erro ao processar CSV: {e}")

    def run_pipeline(self, start_year: int = 2010, end_year: int | None = None):
        """
        Executa o pipeline completo de ingestão.

        Para datasets históricos (DFP, ITR, FRE), itera ano a ano.
        Para CAD, baixa o snapshot atual em arquivo único.
        """
        if end_year is None:
            end_year = datetime.datetime.now().year

        self.setup_database()

        if self.config["is_historical"]:
            logger.info("Iniciando pipeline histórico: %s (%d–%d)", self.dataset_type, start_year, end_year)
            files_to_process = [
                {
                    "year": year,
                    "filename": f"{self.config['file_prefix']}_{year}.zip",
                    "url": f"{self.config['base_url']}{self.config['file_prefix']}_{year}.zip",
                }
                for year in range(start_year, end_year + 1)
            ]
        else:
            logger.info("Iniciando pipeline snapshot atual: %s", self.dataset_type)
            ext = ".zip" if self.config["is_zip"] else ".csv"
            filename = f"{self.config['file_prefix']}{ext}"
            # ano=0 indica snapshot sem referência anual (exibido como "ATUAL" nos logs)
            files_to_process = [
                {"year": 0, "filename": filename, "url": f"{self.config['base_url']}{filename}"}
            ]

        for item in files_to_process:
            year, file_name, url = item["year"], item["filename"], item["url"]

            if self._check_already_processed(file_name):
                logger.info("Arquivo já processado, pulando: %s", file_name)
                continue

            try:
                self._log("INFO", year, file_name, "-", "Iniciando download...")
                response = requests.get(url, headers=self.headers, timeout=120)

                if response.status_code != 200:
                    self._log("ERROR", year, file_name, "-", f"HTTP {response.status_code} — URL: {url}")
                    continue

                if self.config["is_zip"]:
                    try:
                        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                            csv_files = [f for f in z.namelist() if f.endswith(".csv")]
                            if not csv_files:
                                self._log("WARNING", year, file_name, "-", "ZIP sem arquivos CSV.")
                                continue
                            for csv in csv_files:
                                with z.open(csv) as f:
                                    self._process_csv(csv, f, year, file_name)
                    except zipfile.BadZipFile:
                        self._log("ERROR", year, file_name, "-", "Arquivo corrompido ou não é um ZIP válido.")
                        continue
                else:
                    self._process_csv(file_name, io.BytesIO(response.content), year, file_name)

                self._log("SUCCESS", year, file_name, "TODAS", "Processamento do arquivo finalizado.")

            except Exception as e:
                self._log("FATAL", year, file_name, "-", f"Erro crítico inesperado: {e}")


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline Bronze — Ingestão de dados brutos da CVM")
    parser.add_argument(
        "--dataset",
        required=True,
        choices=["FRE", "DFP", "ITR", "CAD"],
        help="Tipo de dataset a ingerir.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2010,
        help="Ano inicial para datasets históricos (padrão: 2010).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Ano final para datasets históricos (padrão: ano atual).",
    )
    args = parser.parse_args()

    _validate_env()

    pipeline = CVMLakeBuilder(dataset_type=args.dataset)
    pipeline.run_pipeline(start_year=args.start_year, end_year=args.end_year)
