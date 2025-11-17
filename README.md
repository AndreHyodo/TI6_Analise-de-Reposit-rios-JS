# Trabalho Interdisciplinar 6 (TI6)

Este projeto...

## Pré-requisitos

- Python 3.10 ou superior
- `pip` (gerenciador de pacotes do Python)

## Estrutura do Projeto

```
TI6/
├── .env
├── app/
│   └── main.py
├── requirements.txt
└── README.md
```

## Guia de Instalação

Siga os passos abaixo para preparar o ambiente e executar a aplicação.

1. Crie e ative um ambiente virtual (recomendado)

- macOS/Linux:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```
- Windows (PowerShell):
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  ```

2. Instale as dependências

```bash
pip install -r requirements.txt
```

3. Configure as variáveis de ambiente

- Duplique o arquivo de exemplo e edite:
  ```bash
  cp .env.sample .env
  ```
- Abra o arquivo `.env` e defina seu token do GitHub:
  ```env
  GITHUB_TOKEN=seu_token_aqui
  ```

4. Execute a aplicação

```bash
python app/main.py
```

## Desenvolvimento

- Ativar ambiente virtual: `source .venv/bin/activate` (macOS/Linux) ou `.venv\Scripts\Activate.ps1` (Windows)
- Desativar ambiente virtual: `deactivate`
