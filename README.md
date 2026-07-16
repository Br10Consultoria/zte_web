# ZTE Titan Manager

Sistema web para gerenciamento, monitoramento, backup e provisionamento de ONUs em OLTs ZTE.

## Padrao Oficial

O projeto usa **Docker Compose** como unico modo oficial de instalacao e operacao.

O modo antigo `systemd + venv` foi descontinuado para evitar diferenca de dependencias, Python, Redis e comandos de atualizacao entre servidores.

## Servicos

| Servico | Descricao |
|---|---|
| `zte_titan_app` | Backend FastAPI + frontend estatico |
| `zte_titan_redis` | Cache Redis |
| `zte_titan_app_data` | Volume persistente da aplicacao, SQLite e backups |
| `zte_titan_redis_data` | Volume persistente do Redis |

## Requisitos

Servidor Debian/Ubuntu com acesso root.

O instalador instala Docker e Docker Compose se necessario.

## Instalacao

```bash
git clone https://github.com/Br10Consultoria/zte_titan.git /opt/zte_titan
cd /opt/zte_titan
bash install.sh
```

Acesse:

```text
http://IP_DO_SERVIDOR:8000
```

Usuario inicial:

```text
admin
Admin2024
```

Altere a senha apos o primeiro acesso.

## Atualizacao Padrao

```bash
cd /opt/zte_titan
git pull
bash install.sh
```

O instalador:

- Mantem o arquivo `.env` existente.
- Desativa o servico legado `zte-titan.service`, se existir.
- Reconstroi a imagem da aplicacao.
- Sobe os containers com o projeto Compose `zte_titan`.

## Comandos Operacionais

```bash
cd /opt/zte_titan

docker compose -p zte_titan ps
docker compose -p zte_titan logs -f app
docker compose -p zte_titan restart app
docker compose -p zte_titan down
docker compose -p zte_titan up -d
```

## Reset Total

Para apagar containers, volumes e cache local do Docker:

```bash
cd /opt/zte_titan
docker compose -p zte_titan down -v
rm -rf data venv
bash install.sh
```

## Funcionalidades

| Funcionalidade | Descricao |
|---|---|
| Autenticacao | Login com usuario/senha e 2FA TOTP |
| OLTs | Cadastro, teste de conexao e descoberta de portas |
| Cache Redis | Consultas salvas por 1 hora, com atualizacao manual |
| Status das ONUs | Estado, admin, uptime, RX ONU, RX OLT e filtros |
| Detalhes da ONU | Historico, potencia, firmware, WAN, VLAN, vendor e SFP PON |
| Busca por Serial | Localizacao por serial, modelo, PON e OLT |
| Dashboard | Graficos por PON, estado, sinal, modelo, firmware e marca |
| Provisionamento | ONUs nao provisionadas e templates de provisionamento |
| Backup | Backup via FTP integrado e notificacao Telegram |

## Faixas de Sinal

| Sinal | Faixa | Status |
|---|---|---|
| RX ONU / RX OLT | `>= -27 dBm` | Normal |
| RX ONU / RX OLT | `> -29 dBm` e `< -27 dBm` | Atencao |
| RX ONU / RX OLT | `<= -29 dBm` | Critico |

## OLTs Suportadas

| Modelo | Observacao |
|---|---|
| ZTE C600 | Usa formato `gpon_olt-...` |
| ZTE C300/C320 | Usa formato `gpon-olt_...` em comandos especificos |

## NETCONF

O sistema atualmente usa Telnet/SSH porque esses comandos estao disponiveis nas OLTs testadas e retornam as informacoes necessarias.

NETCONF so deve virar prioridade se o modelo e firmware da OLT tiverem suporte real e permissao habilitada. Ele pode melhorar padronizacao e estrutura dos dados, mas nao garante ganho relevante de desempenho sozinho: hoje o maior custo esta no tempo de resposta da OLT, quantidade de comandos e cache. O caminho mais seguro para performance continua sendo Redis, atualizacao por hora e consultas sob demanda.

## Estrutura

```text
zte_titan/
  backend/
    app/
    Dockerfile
    requirements.txt
  frontend/
    index.html
    static/js/app.js
  docker-compose.yml
  install.sh
  start.sh
  README.md
```
