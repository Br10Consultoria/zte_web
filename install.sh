#!/bin/bash
# ============================================================
# ZTE Titan Manager — Instalador Completo
# Debian 13 (Trixie) — Máquina zerada, sem nada instalado
# Execute como root: bash install.sh
# ============================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
info() { echo -e "${BLUE}[..] $1${NC}"; }
warn() { echo -e "${YELLOW}[AV] $1${NC}"; }
err()  { echo -e "${RED}[ERRO] $1${NC}"; exit 1; }

echo ""
echo "=============================================="
echo "   ZTE Titan Manager — Instalador Completo"
echo "   Debian 13 — $(date '+%d/%m/%Y %H:%M')"
echo "=============================================="
echo ""

# Verifica se é root
if [ "$EUID" -ne 0 ]; then
    err "Execute como root: sudo bash install.sh"
fi

# Detecta o diretório onde o script está
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR"
DATA_DIR="$APP_DIR/data"
VENV_DIR="$APP_DIR/venv"

info "Diretório da aplicação: $APP_DIR"

# ============================================================
# 1. ATUALIZAR O SISTEMA
# ============================================================
info "Atualizando lista de pacotes..."
apt-get update -qq
ok "Lista de pacotes atualizada"

# ============================================================
# 2. INSTALAR DEPENDÊNCIAS DO SISTEMA
# ============================================================
info "Instalando dependências do sistema..."
apt-get install -y -qq \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    redis-server \
    snmp \
    redis-tools \
    gcc \
    libffi-dev \
    libssl-dev \
    curl \
    git \
    net-tools \
    procps \
    nano \
    2>&1 | grep -E "^(Get|Inst|Err)" || true
ok "Dependências do sistema instaladas"

# ============================================================
# 3. CONFIGURAR E INICIAR O REDIS
# ============================================================
info "Configurando Redis..."
systemctl enable redis-server --quiet 2>/dev/null || true
systemctl start redis-server 2>/dev/null || service redis-server start 2>/dev/null || true
sleep 2

if redis-cli ping 2>/dev/null | grep -q "PONG"; then
    ok "Redis rodando corretamente"
else
    warn "Redis não respondeu ao ping — o sistema funcionará sem cache"
fi

# ============================================================
# 4. CRIAR AMBIENTE VIRTUAL PYTHON
# ============================================================
info "Criando ambiente virtual Python..."
if [ -d "$VENV_DIR" ]; then
    rm -rf "$VENV_DIR"
fi
python3 -m venv "$VENV_DIR"
ok "Ambiente virtual criado em $VENV_DIR"

# ============================================================
# 5. INSTALAR DEPENDÊNCIAS PYTHON
# ============================================================
info "Instalando dependências Python (pode demorar alguns minutos)..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools wheel

# Instala dependências sem versões fixas para compatibilidade com Python 3.13+
"$VENV_DIR/bin/pip" install --quiet \
    "fastapi" \
    "uvicorn[standard]" \
    "sqlalchemy" \
    "python-jose[cryptography]" \
    "bcrypt>=4.2.0" \
    "python-multipart" \
    "pyotp" \
    "qrcode[pil]" \
    "redis" \
    "paramiko" \
    "aiofiles" \
    "python-dotenv" \
    "Pillow"
ok "Dependências Python instaladas"

# ============================================================
# 6. CRIAR DIRETÓRIO DE DADOS
# ============================================================
info "Criando diretório de dados..."
mkdir -p "$DATA_DIR"
ok "Diretório $DATA_DIR criado"

# ============================================================
# 7. CRIAR ARQUIVO .env SE NÃO EXISTIR
# ============================================================
if [ ! -f "$APP_DIR/.env" ]; then
    info "Criando arquivo .env..."
    # Gera uma chave secreta aleatória
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "$APP_DIR/.env" << EOF
# ZTE Titan Manager — Configurações
SECRET_KEY=${SECRET}
DATABASE_URL=sqlite:////$(echo $DATA_DIR)/zte_titan.db
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
CACHE_TTL=86400
ACCESS_TOKEN_EXPIRE_MINUTES=480
SSH_TIMEOUT=30
SSH_COMMAND_TIMEOUT=60
EOF
    ok "Arquivo .env criado com chave secreta aleatória"
else
    ok "Arquivo .env já existe — mantendo configurações"
fi

# ============================================================
# 8. CRIAR SERVIÇO SYSTEMD
# ============================================================
info "Criando serviço systemd (zte-titan)..."

cat > /etc/systemd/system/zte-titan.service << EOF
[Unit]
Description=ZTE Titan Manager
After=network.target redis-server.service
Wants=redis-server.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}/backend
Environment=PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=zte-titan

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable zte-titan --quiet
ok "Serviço systemd criado e habilitado"

# ============================================================
# 9. INICIAR O SERVIÇO
# ============================================================
info "Iniciando o serviço ZTE Titan Manager..."
systemctl start zte-titan
sleep 4

if systemctl is-active --quiet zte-titan; then
    ok "Serviço iniciado com sucesso"
else
    warn "Serviço pode estar demorando para iniciar. Verificando logs..."
    journalctl -u zte-titan -n 20 --no-pager 2>/dev/null || true
fi

# ============================================================
# 10. VERIFICAR SE O SISTEMA ESTÁ RESPONDENDO
# ============================================================
info "Verificando se o sistema está respondendo..."
sleep 3
for i in 1 2 3 4 5; do
    if curl -s http://localhost:8000/api/health > /dev/null 2>&1; then
        ok "Sistema respondendo na porta 8000"
        break
    fi
    if [ $i -eq 5 ]; then
        warn "Sistema ainda não respondeu. Verifique com: journalctl -u zte-titan -f"
    fi
    sleep 2
done

# ============================================================
# RESUMO FINAL
# ============================================================
IP_LOCAL=$(hostname -I | awk '{print $1}')
echo ""
echo "=============================================="
echo -e "${GREEN}   INSTALAÇÃO CONCLUÍDA COM SUCESSO!${NC}"
echo "=============================================="
echo ""
echo -e "  ${BLUE}Acesso local:${NC}    http://localhost:8000"
echo -e "  ${BLUE}Acesso na rede:${NC}  http://${IP_LOCAL}:8000"
echo ""
echo -e "  ${YELLOW}Usuário:${NC}  admin"
echo -e "  ${YELLOW}Senha:${NC}    Admin2024"
echo ""
echo "  IMPORTANTE: Altere a senha após o primeiro login!"
echo ""
echo "  Comandos úteis:"
echo "    Ver status:   systemctl status zte-titan"
echo "    Ver logs:     journalctl -u zte-titan -f"
echo "    Reiniciar:    systemctl restart zte-titan"
echo "    Parar:        systemctl stop zte-titan"
echo "    API Docs:     http://${IP_LOCAL}:8000/api/docs"
echo ""
echo "=============================================="
