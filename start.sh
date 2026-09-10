#!/bin/bash
# Agente WhatsApp — script de arranque do stack Docker.
# O agente corre APENAS como container Docker (serviço "agente"), na rede do
# docker-compose — nunca como uvicorn no host.

set -e

# Diretório do próprio script (independente do utilizador/máquina)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$ROOT/agente.log"
EVOLUTION_DIR="$ROOT/evolution"

echo "$(date) - A iniciar Agente WhatsApp..." >> "$LOG"

# 1. Aguarda o serviço Docker (systemd) estar disponível
for i in $(seq 1 60); do
    if docker info > /dev/null 2>&1; then
        echo "$(date) - Docker disponível." >> "$LOG"
        break
    fi
    echo "$(date) - Aguardando Docker... ($i/60)" >> "$LOG"
    sleep 5
done

if ! docker info > /dev/null 2>&1; then
    echo "$(date) - Docker indisponível após timeout. Verifica: sudo systemctl status docker" >> "$LOG"
    exit 1
fi

# 2. Arranca Evolution API + postgres + redis + agente
echo "$(date) - A iniciar stack Docker..." >> "$LOG"
cd "$EVOLUTION_DIR"
docker compose up -d --build >> "$LOG" 2>&1

# 3. Aguarda a Evolution API estar pronta
for i in $(seq 1 40); do
    if curl -s --max-time 3 http://localhost:8080/ > /dev/null 2>&1; then
        echo "$(date) - Evolution API pronta." >> "$LOG"
        break
    fi
    echo "$(date) - Aguardando Evolution API... ($i/40)" >> "$LOG"
    sleep 3
done

# 4. Aguarda o container do agente arrancar
for i in $(seq 1 30); do
    if docker logs agente 2>&1 | grep -q "Application startup complete"; then
        echo "$(date) - Agente pronto (container)." >> "$LOG"
        break
    fi
    echo "$(date) - Aguardando agente... ($i/30)" >> "$LOG"
    sleep 2
done

echo "$(date) - ✅ Tudo online!" >> "$LOG"
