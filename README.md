# Democratização de Dados da CVM: Pipeline End-to-End para Informações Contábeis de Empresas Abertas no Brasil

Periodicamente, milhares de empresas submetem seus dados financeiros à Comissão de Valores Mobiliários (CVM) por meio do portal de dados abertos. Contudo, a mera disponibilização de arquivos brutos não garante a acessibilidade e a compreensão pública dessas informações.

Este projeto transforma esses dados brutos em conhecimento acionável, entregando um repositório público e um dashboard analítico que traduzem os principais indicadores financeiros das empresas de capital aberto no Brasil.

A metodologia segue o processo de **Knowledge Discovery in Databases (KDD)** estruturado sob a **Arquitetura Medalhão** (Bronze → Silver → Gold), com ecossistema 100% open-source.

---

## Tecnologias utilizadas

| Ferramenta | Papel no projeto |
|---|---|
| Python | Orquestração e curadoria dos dados |
| PostgreSQL | Armazenamento relacional |
| DBeaver | Interface visual para o banco de dados |
| Streamlit | Dashboard interativo |
| Git + GitHub | Versionamento e distribuição do código |

---

## Pré-requisitos

Antes de começar, você precisará instalar as ferramentas abaixo. Cada uma tem um vídeo explicando a instalação passo a passo.

### 1. Python

<!-- VIDEO: instalacao-python -->
> Em breve: vídeo ensinando a instalar o Python no Windows.

Baixe em: https://www.python.org/downloads/

> Importante: durante a instalação, marque a opção **"Add Python to PATH"**.

---

### 2. PostgreSQL

<!-- VIDEO: instalacao-postgresql -->
> Em breve: vídeo ensinando a instalar o PostgreSQL no Windows.

Baixe em: https://www.postgresql.org/download/

> Anote a senha que você definir para o usuário `postgres` — você vai precisar dela.

---

### 3. DBeaver

<!-- VIDEO: instalacao-dbeaver -->
> Em breve: vídeo ensinando a instalar e configurar o DBeaver.

Baixe em: https://dbeaver.io/download/

---

### 4. Git for Windows

<!-- VIDEO: instalacao-git -->
> Em breve: vídeo ensinando a instalar o Git no Windows.

Baixe em: https://git-scm.com/download/win

---

### 5. Conta no GitHub

<!-- VIDEO: criacao-github -->
> Em breve: vídeo ensinando a criar uma conta no GitHub.

Crie sua conta em: https://github.com

---

## Instalação do projeto

### 1. Clone o repositório

Abra o terminal (PowerShell ou CMD) e rode:

```bash
git clone https://github.com/ivanrmello/cvm_data_for_all.git
cd cvm_data_for_all
```

<!-- VIDEO: clone-repositorio -->
> Em breve: vídeo ensinando a clonar o repositório e navegar até a pasta.

---

### 2. Crie o ambiente virtual e instale as dependências

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -q
```

<!-- VIDEO: criacao-venv -->
> Em breve: vídeo ensinando a criar o ambiente virtual e instalar os pacotes.

---

### 3. Configure as variáveis de ambiente

Copie o arquivo de exemplo e preencha com suas credenciais:

```bash
copy .env.example .env
```

Abra o arquivo `.env` em qualquer editor de texto e preencha:

```env
DB_USER=postgres
DB_PASS=sua_senha_aqui
DB_HOST=localhost
DB_PORT=5432
DB_NAME=cvm_data_for_all
```

> O banco de dados será criado automaticamente na primeira execução — você não precisa criá-lo manualmente no DBeaver.

<!-- VIDEO: configuracao-env -->
> Em breve: vídeo ensinando a configurar o arquivo .env.

---

## Rodando o pipeline

O pipeline é dividido em três camadas independentes que devem ser executadas em sequência.

### Camada Bronze — Coleta de dados brutos

A camada Bronze baixa os arquivos diretamente do portal de dados abertos da CVM e carrega no PostgreSQL sem nenhuma transformação.

Escolha o tipo de documento e rode:

```bash
# Formulário de Referência
python bronze/cvm_bronze.py --dataset FRE --start-year 2010

# Demonstrações Financeiras Padronizadas
python bronze/cvm_bronze.py --dataset DFP --start-year 2010

# Informações Trimestrais
python bronze/cvm_bronze.py --dataset ITR --start-year 2010

# Cadastro de Companhias Abertas (arquivo único, sem ano)
python bronze/cvm_bronze.py --dataset CAD
```

<!-- VIDEO: rodando-bronze -->
> Em breve: vídeo demonstrando a execução da camada Bronze.

> **Idempotência:** o pipeline é seguro para ser rodado mais de uma vez — arquivos já processados são automaticamente ignorados.

---

### Camada Silver — Limpeza e curadoria

> Em breve.

<!-- VIDEO: rodando-silver -->
> Em breve: vídeo demonstrando a execução da camada Silver.

---

### Camada Gold — Modelagem analítica

> Em breve.

<!-- VIDEO: rodando-gold -->
> Em breve: vídeo demonstrando a execução da camada Gold.

---

### Dashboard

> Em breve.

<!-- VIDEO: dashboard -->
> Em breve: vídeo demonstrando o uso do dashboard em Streamlit.

---

## Arquitetura do projeto

```
cvm_data_for_all/
├── bronze/          # Coleta e carga dos dados brutos (CVM → PostgreSQL)
├── silver/          # Limpeza, curadoria e harmonização
├── gold/            # Modelagem analítica e indicadores financeiros
├── dashboard/       # Dashboard interativo em Streamlit
├── .env.example     # Template de configuração
└── requirements.txt # Dependências do projeto
```

---

## Sobre o projeto

Este projeto foi desenvolvido como ação de extensão universitária no âmbito do programa **UFMS Digital**, com o objetivo de democratizar o acesso às informações contábeis das empresas de capital aberto no Brasil.

**Palavras-chave:** Engenharia de Dados · Arquitetura Medalhão · CVM · Inteligência Financeira · Python · Open Source
