"""
Camada Silver — Entrypoint CLI.

Orquestra os 5 steps de transformação dos dados brutos (Bronze)
em dados curados e validados (Silver).

Uso:
    python silver/cvm_silver.py --step all
    python silver/cvm_silver.py --step empresas
    python silver/cvm_silver.py --step consolidar
    python silver/cvm_silver.py --step bp
    python silver/cvm_silver.py --step dre
    python silver/cvm_silver.py --step dfc

Dependências obrigatórias (em ordem):
    empresas → consolidar → bp / dre / dfc
"""

import argparse
import logging
import os
import sys

# Garante que a raiz do projeto está no sys.path,
# independente de onde o script é chamado.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

STEPS_DISPONIVEIS = ["all", "empresas", "consolidar", "bp", "dre", "dfc"]

ORDEM_EXECUCAO = ["empresas", "consolidar", "bp", "dre", "dfc"]

DESCRICAO_STEPS = {
    "empresas":   "Step 1 — Seleciona empresas elegíveis (ativas, bolsa, operacionais)",
    "consolidar": "Step 2 — Consolida 7 tabelas DFP Bronze em n0_dfp_cia_aberta",
    "bp":         "Step 3 — Balanço Patrimonial com hierarquia e validação matemática",
    "dre":        "Step 4 — DRE com Golden Map e validação de consistência",
    "dfc":        "Step 5 — DFC com Golden Map e validação de equação de caixa",
}


def importar_step(nome: str):
    """Importa o módulo do step solicitado."""
    if nome == "empresas":
        from silver.steps import s1_empresas as mod
    elif nome == "consolidar":
        from silver.steps import s2_consolidar as mod
    elif nome == "bp":
        from silver.steps import s3_bp as mod
    elif nome == "dre":
        from silver.steps import s4_dre as mod
    elif nome == "dfc":
        from silver.steps import s5_dfc as mod
    else:
        raise ValueError(f"Step desconhecido: {nome}")
    return mod


def executar_step(nome: str):
    """Importa e executa o step, capturando erros individualmente."""
    logger.info("=" * 60)
    logger.info("Iniciando: %s", DESCRICAO_STEPS[nome])
    logger.info("=" * 60)
    try:
        mod = importar_step(nome)
        mod.run()
    except Exception as exc:
        logger.exception("Erro ao executar step '%s': %s", nome, exc)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline Silver — CVM Data For All",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python silver/cvm_silver.py --step all          # executa todos em sequência
  python silver/cvm_silver.py --step empresas     # apenas step 1
  python silver/cvm_silver.py --step bp           # apenas step 3 (requer s1+s2)

Atenção: os steps têm dependências obrigatórias:
  empresas → consolidar → bp, dre, dfc
        """,
    )
    parser.add_argument(
        "--step",
        required=True,
        choices=STEPS_DISPONIVEIS,
        help="Step a executar. Use 'all' para rodar todos em ordem.",
    )

    args = parser.parse_args()

    # Garante que o schema Silver existe antes de qualquer escrita
    from silver.utils import create_db_engine, ensure_silver_schema
    ensure_silver_schema(create_db_engine())

    if args.step == "all":
        logger.info("Executando todos os steps em sequência...")
        for nome in ORDEM_EXECUCAO:
            executar_step(nome)
        logger.info("Pipeline Silver concluído com sucesso.")
    else:
        executar_step(args.step)


if __name__ == "__main__":
    main()
