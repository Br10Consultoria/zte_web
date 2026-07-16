#!/bin/bash
# ============================================================
# ZTE Titan Manager - Docker-only installer
# Debian/Ubuntu server
# Execute como root: bash install.sh
# ============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${BLUE}[..] $1${NC}"; }
warn() { echo -e "${YELLOW}[AV]${NC} $1"; }
err()  { echo -e "${RED}[ERRO]${NC} $1"; exit 1; }

if [ "${EUID}" -ne 0 ]; then
    err "Execute como root: sudo bash install.sh"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-zte_titan}"
APP_VOLUME="${PROJECT_NAME}_app_data"
REDIS_VOLUME="${PROJECT_NAME}_redis_data"

cd "$APP_DIR"

echo ""
echo "=============================================="
echo "   ZTE Titan Manager - Instalador Docker"
echo "   $(date '+%d/%m/%Y %H:%M')"
echo "=============================================="
echo ""

info "Diretorio da aplicacao: $APP_DIR"

info "Atualizando pacotes e instalando dependencias Docker..."
apt-get update -qq
apt-get install -y -qq \
    ca-certificates \
    curl \
    git \
    python3 \
    docker.io

if ! apt-get install -y -qq docker-compose-plugin; then
    warn "docker-compose-plugin nao encontrado no repositorio. Tentando docker-compose classico..."
    apt-get install -y -qq docker-compose
fi
ok "Dependencias do sistema instaladas"

info "Habilitando Docker..."
systemctl enable docker --quiet 2>/dev/null || true
systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true

if ! docker version >/dev/null 2>&1; then
    err "Docker nao respondeu. Verifique a instalacao do Docker."
fi

if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose -p "$PROJECT_NAME")
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose -p "$PROJECT_NAME")
else
    err "Docker Compose nao encontrado."
fi
ok "Docker e Docker Compose disponiveis"

if systemctl list-unit-files 2>/dev/null | grep -q '^zte-titan.service'; then
    warn "Servico legado systemd encontrado. Ele sera desativado para evitar conflito na porta 8000."
    systemctl stop zte-titan 2>/dev/null || true
    systemctl disable zte-titan 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
fi

if [ ! -f "$APP_DIR/.env" ]; then
    info "Criando .env a partir do .env.example..."
    if [ -f "$APP_DIR/.env.example" ]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    else
        touch "$APP_DIR/.env"
    fi
    SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)"
    if grep -q '^SECRET_KEY=' "$APP_DIR/.env"; then
        sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${SECRET}|" "$APP_DIR/.env"
    else
        echo "SECRET_KEY=${SECRET}" >> "$APP_DIR/.env"
    fi
    ok ".env criado com SECRET_KEY aleatoria"
else
    ok ".env existente mantido"
fi

if ! grep -q '^COMPOSE_PROJECT_NAME=' "$APP_DIR/.env"; then
    echo "COMPOSE_PROJECT_NAME=${PROJECT_NAME}" >> "$APP_DIR/.env"
fi

mkdir -p "$APP_DIR/data"

info "Preparando volumes Docker..."
docker volume create "$APP_VOLUME" >/dev/null
docker volume create "$REDIS_VOLUME" >/dev/null

info "Construindo imagem da aplicacao..."
"${COMPOSE[@]}" build app

info "Subindo containers..."
"${COMPOSE[@]}" up -d

info "Verificando saude da API..."
sleep 5
for i in 1 2 3 4 5 6; do
    if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
        ok "Sistema respondendo em http://localhost:8000"
        break
    fi
    if [ "$i" -eq 6 ]; then
        warn "A API ainda nao respondeu. Veja os logs com: docker compose -p ${PROJECT_NAME} logs -f app"
    fi
    sleep 3
done

IP_LOCAL="$(hostname -I | awk '{print $1}')"
echo ""
echo "=============================================="
echo -e "${GREEN}   INSTALACAO DOCKER CONCLUIDA${NC}"
echo "=============================================="
echo ""
echo -e "  ${BLUE}Acesso local:${NC}    http://localhost:8000"
echo -e "  ${BLUE}Acesso na rede:${NC}  http://${IP_LOCAL}:8000"
echo ""
echo "  Comandos uteis:"
echo "    Status:      docker compose -p ${PROJECT_NAME} ps"
echo "    Logs:        docker compose -p ${PROJECT_NAME} logs -f app"
echo "    Atualizar:   git pull && bash install.sh"
echo "    Reiniciar:   docker compose -p ${PROJECT_NAME} restart app"
echo "    Parar:       docker compose -p ${PROJECT_NAME} down"
echo ""
echo "=============================================="
