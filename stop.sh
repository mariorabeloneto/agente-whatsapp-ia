#!/bin/bash
# Para a stack Docker do Agente WhatsApp (Evolution API + agente)

set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "A parar stack Docker..."
cd "$ROOT/evolution"
docker compose down
echo "✅ Tudo parado."
