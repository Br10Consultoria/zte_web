# Arquitetura do Sistema de Gerenciamento ZTE Titan

Este documento descreve a arquitetura e a estrutura do sistema web para gerenciamento de ONUs na OLT ZTE Titan.

## Tecnologias Utilizadas

- **Backend**: Python 3.11 com **FastAPI** (rápido, assíncrono, documentação automática).
- **Banco de Dados**: **SQLite** (armazenamento local simples, sem necessidade de servidor de BD dedicado).
- **Cache**: **Redis** (cache de 1 hora para os resultados das consultas).
- **Autenticação**: JWT (JSON Web Tokens) + **2FA TOTP** (Google Authenticator / Authy) com QR Code.
- **Frontend**: HTML5, **Tailwind CSS** (estilizado para fontes de no máximo 12px, tema escuro/profissional), **Alpine.js** (reatividade simples sem necessidade de build complexo).
- **Integração OLT**: SSH (via `paramiko`) e SNMP (via `pysnmp` ou consultas diretas).
- **Containerização**: **Docker** e **Docker Compose** como padrao unico de implantacao.

## Estrutura de Diretórios

```
zte_titan/
│
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py            # Ponto de entrada FastAPI
│   │   ├── config.py          # Configurações globais
│   │   ├── database.py        # Conexão SQLite e modelos SQLAlchemy
│   │   ├── models.py          # Modelos do banco de dados
│   │   ├── schemas.py         # Schemas Pydantic para validação
│   │   ├── auth.py            # Lógica de JWT e 2FA (TOTP)
│   │   ├── redis_client.py    # Integração com Redis para Cache
│   │   ├── olt_client.py      # Conexão SSH/SNMP com ZTE Titan
│   │   └── routes/
│   │       ├── auth.py        # Rotas de login, registro, 2FA
│   │       ├── olts.py        # Rotas de gerenciamento de OLTs e Descoberta
│   │       └── onus.py        # Rotas de consulta de ONUs
│   │
│   ├── requirements.txt       # Dependências Python
│   └── Dockerfile             # Dockerfile para o backend
│
├── frontend/                  # Arquivos estáticos servidos pelo FastAPI
│   ├── index.html             # Dashboard / Login
│   ├── css/
│   │   └── style.css          # Customizações (font-size <= 12px)
│   └── js/
│       ├── app.js             # Lógica de chamadas de API
│       └── alpine.js          # Alpine.js local ou CDN
│
├── docker-compose.yml         # Orquestração do App + Redis
├── README.md                  # Instruções de instalação e uso
└── docs_reference.md          # Referência de comandos ZTE Titan
```

## Modelagem do Banco de Dados (SQLite)

### Tabela: `users`
- `id` (INTEGER, PK)
- `username` (VARCHAR, UNIQUE)
- `password_hash` (VARCHAR)
- `role` (VARCHAR) - `admin` ou `viewer`
- `totp_secret` (VARCHAR) - Segredo para o 2FA
- `is_2fa_enabled` (BOOLEAN) - Se o 2FA já foi ativado

### Tabela: `olts`
- `id` (INTEGER, PK)
- `name` (VARCHAR)
- `ip` (VARCHAR)
- `port` (INTEGER)
- `username` (VARCHAR)
- `password` (VARCHAR)
- `protocol` (VARCHAR) - `ssh` ou `snmp`
- `snmp_community` (VARCHAR, NULL)
- `status` (VARCHAR) - `online` ou `offline`

### Tabela: `olt_ports` (Descoberta)
- `id` (INTEGER, PK)
- `olt_id` (INTEGER, FK -> olts.id)
- `slot` (INTEGER)
- `port` (INTEGER)
- `type` (VARCHAR) - ex: `gpon`
- `description` (VARCHAR, NULL)

## Mecanismo de Cache (Redis)
- Toda consulta de status de ONUs por porta PON (`show gpon onu state`) ou detalhada (`show gpon onu detail-info`, `show pon power attenuation`) será armazenada no Redis.
- Chave do Redis: `olt:{olt_id}:pon:{slot}:{port}:onus` ou `olt:{olt_id}:onu:{slot}:{port}:{onu_id}:detail`
- Tempo de expiração (TTL): 3600 segundos (1 hora).
- Possibilidade de "Forçar Atualização" no frontend para limpar o cache e consultar diretamente na OLT.
