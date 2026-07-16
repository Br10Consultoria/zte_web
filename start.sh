#!/bin/bash
# Docker-only startup helper.

set -euo pipefail

PROJECT_NAME="${COMPOSE_PROJECT_NAME:-zte_titan}"

if docker compose version >/dev/null 2>&1; then
    docker compose -p "$PROJECT_NAME" up -d
elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -p "$PROJECT_NAME" up -d
else
    echo "Docker Compose nao encontrado. Execute: sudo bash install.sh"
    exit 1
fi

echo "ZTE Titan Manager iniciado em http://localhost:8000"
echo "Logs: docker compose -p ${PROJECT_NAME} logs -f app"
