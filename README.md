# ZTE Titan Manager

Sistema web profissional para gerenciamento e monitoramento de ONUs em OLTs **ZTE Titan** (Série C600/C610/C620/C650 / Multilaser PRO).

---

## Funcionalidades

| Funcionalidade | Descrição |
|---|---|
| **Autenticação segura** | Login com usuário/senha + 2FA TOTP (QR Code) |
| **Multi-usuários** | Perfis Admin e Visualizador |
| **Cadastro de OLTs** | IP, porta, usuário, senha, protocolo (SSH/Telnet) |
| **Scan de Descoberta** | Detecta automaticamente slots e portas PON da OLT |
| **Status das ONUs** | Estado operacional, admin state, última causa de queda |
| **Potência Óptica** | RX/TX da ONU e RX da OLT com indicadores de cor |
| **Detalhes da ONU** | Serial, firmware, temperatura, WAN, VoIP, distância |
| **ONUs Não Provisionadas** | Lista ONUs aguardando autorização |
| **Busca por Serial** | Localiza ONU em todas as portas PON |
| **Cache Redis 1h** | Resultados armazenados por 1 hora, com opção de forçar atualização |
| **Impressão** | Exporta tabela de ONUs para impressão diretamente no navegador |
| **Interface moderna** | Tema escuro profissional, fontes máx. 12px, responsivo |

---

## Pré-requisitos

### Com Docker (recomendado)
- Docker Engine 20+
- Docker Compose 2+

### Sem Docker
- Python 3.11+
- Redis 6+ (opcional, mas recomendado para cache)

---

## Instalação

### Opção 1 — Docker Compose (recomendado)

```bash
# Clone o repositório
git clone https://github.com/Br10Consultoria/zte_titan.git
cd zte_titan

# Copie e configure o arquivo de ambiente
cp .env.example .env
# Edite o .env e altere o SECRET_KEY!

# Inicie os serviços
docker compose up -d

# Verifique os logs
docker compose logs -f app
```

Acesse: **http://localhost:8000**

---

### Opção 2 — Execução direta (sem Docker)

```bash
# Clone o repositório
git clone https://github.com/Br10Consultoria/zte_titan.git
cd zte_titan

# Instale o Redis (Ubuntu/Debian)
sudo apt update && sudo apt install -y redis-server
sudo systemctl start redis

# Execute o script de inicialização
chmod +x start.sh
./start.sh
```
## Primeiro Acesso

| Campo | Valor |
|---|---|
| **URL** | http://localhost:8000 |
| **Usuário** | `admin` |
| **Senha** | `Admin2024` |

> **IMPORTANTE:** Altere a senha do admin imediatamente após o primeiro login.

---

## Configuração do 2FA

1. Faça login com usuário e senha
2. Vá em **Meu Perfil** → **Configurar 2FA**
3. Escaneie o QR Code com **Google Authenticator**, **Authy** ou outro app TOTP
4. Confirme com o código gerado pelo aplicativo
5. No próximo login, será solicitado o código do aplicativo

---

## Uso do Sistema

### 1. Cadastrar uma OLT

1. Acesse **OLTs** → **Nova OLT**
2. Preencha: Nome, IP, Porta (22 para SSH, 23 para Telnet), Usuário, Senha, Protocolo
3. Clique em **Salvar**
4. Clique no ícone **Testar Conexão** para verificar a conectividade
5. Clique em **Descobrir Portas** (ícone radar) para detectar os slots e portas PON

### 2. Consultar ONUs

1. Acesse **ONUs**
2. Selecione a OLT e a porta PON desejada
3. Clique em **Consultar**
4. Os dados ficam em cache por 1 hora. Use **Atualizar** para forçar nova consulta

### 3. Ver Detalhes de uma ONU

1. Na tabela de ONUs, clique em **Detalhes**
2. Navegue pelas abas: Estado, Detalhes, Potência, Distância, WAN, VoIP, Temperatura, Firmware

### 4. Criar Usuários

1. Acesse **Usuários** (apenas Admin)
2. Clique em **Novo Usuário**
3. Defina perfil: **Administrador** (acesso total) ou **Visualizador** (somente consulta)

---

## Referência de Comandos SSH (ZTE Titan)

| Comando | Descrição |
|---|---|
| `show gpon onu state gpon-olt_X/1/Y` | Status de todas as ONUs da PON |
| `show gpon onu detail-info gpon-onu_X/1/Y:Z` | Detalhes completos da ONU |
| `show pon power attenuation gpon-onu_X/1/Y:Z` | Potência óptica da ONU |
| `show pon power olt-rx gpon-olt_X/1/Y` | Potência recebida pela OLT |
| `show gpon onu distance gpon-onu_X/1/Y:Z` | Distância da ONU |
| `show gpon remote-onu wan-info gpon-onu_X/1/Y:Z` | Status WAN |
| `show gpon remote-onu voip-status gpon-onu_X/1/Y:Z` | Status VoIP |
| `show gpon onu temperature gpon-onu_X/1/Y:Z` | Temperatura |
| `show gpon onu firmware-version gpon-onu_X/1/Y:Z` | Firmware |
| `show gpon onu uncfg` | ONUs não provisionadas |

---

## Faixas de Sinal

| Sinal | Faixa | Status |
|---|---|---|
| RX ONU | -8 a -27 dBm | Normal |
| RX ONU | -27 a -29 dBm | Atenção |
| RX ONU | < -29 dBm | Crítico |
| RX OLT | -10 a -25 dBm | Normal |
| RX OLT | -25 a -28 dBm | Atenção |
| RX OLT | < -28 dBm | Crítico |

---

## Causas de Queda (Last Down Cause)

| Causa | Significado | Ação |
|---|---|---|
| DyingGasp | Falta de energia na ONU | Verificar fonte/tomada |
| LOS | Perda de sinal óptico | Verificar fibra/conectores |
| LOF | Perda de sincronismo GPON | Acionar suporte N2 |
| PowerOff | ONU desligada | Verificar energia |
| Reboot | Reinicialização | Aguardar estabilização |
| OMCI Down | Falha de comunicação OLT-ONU | Acionar suporte N2 |
| Deactive | ONU removida/desautorizada | Verificar provisionamento |
| OLT Reset | Reinicialização da OLT | Aguardar estabilização |

---

## Estrutura do Projeto

```
zte_titan/
├── backend/
│   ├── app/
│   │   ├── main.py          # Ponto de entrada FastAPI
│   │   ├── config.py        # Configurações
│   │   ├── database.py      # SQLite + SQLAlchemy
│   │   ├── models.py        # Modelos do banco
│   │   ├── schemas.py       # Schemas Pydantic
│   │   ├── auth.py          # JWT + 2FA TOTP
│   │   ├── redis_client.py  # Cache Redis
│   │   ├── olt_client.py    # SSH/Telnet + Parsers ZTE
│   │   └── routes/
│   │       ├── auth.py      # Autenticação e usuários
│   │       ├── olts.py      # Gerenciamento de OLTs
│   │       └── onus.py      # Consulta de ONUs
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── index.html           # Interface principal (SPA)
│   └── static/js/app.js    # Lógica Alpine.js
├── docker-compose.yml
├── .env.example
├── start.sh
└── README.md
```

---

## API REST

A documentação interativa da API está disponível em:

- **Swagger UI**: http://localhost:8000/api/docs
- **ReDoc**: http://localhost:8000/api/redoc

---

## Tecnologias

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Banco de Dados | SQLite (via SQLAlchemy) |
| Cache | Redis 7 |
| Autenticação | JWT + TOTP (pyotp) |
| Comunicação OLT | SSH (paramiko) + Telnet |
| Frontend | HTML5 + Tailwind CSS + Alpine.js |
| Containerização | Docker + Docker Compose |

---

## Suporte

Compatível com OLTs **ZTE Titan** da linha Multilaser PRO:
- C600, C610, C620, C650

---

*Desenvolvido para uso interno — Br10 Consultoria*
