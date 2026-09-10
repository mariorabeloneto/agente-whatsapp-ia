#!/bin/bash
# apagar_dados.sh — apaga TODOS os dados de um titular (RGPD art. 17, direito ao
# esquecimento). Corre no servidor e cobre: Redis, Airtable, Calendar e as
# mensagens guardadas na Evolution API (Postgres).
#
# Uso:  ./apagar_dados.sh 351913326279
#       ./apagar_dados.sh 351913326279 --dry    # mostra o que apagaria
set -e

NUM="$1"
DRY="$2"
[ -z "$NUM" ] && { echo "uso: $0 <numero> [--dry]"; exit 1; }

DIG=$(echo "$NUM" | tr -cd '0-9')
JID="${DIG}@s.whatsapp.net"

echo "════════════════════════════════════════════"
echo "🗑️  Apagar dados de ${DIG} ${DRY:+[DRY-RUN]}"
echo "════════════════════════════════════════════"

echo "▶ 1) Redis + Airtable + Calendar (dentro do container)"
docker exec agente python privacidade.py "${DIG}" ${DRY}

echo "▶ 2) Evolution API (mensagens/chats/contactos no Postgres)"
if [ "$DRY" = "--dry" ]; then
    docker exec evolution-postgres psql -U evolution -d evolution -t -c \
      "SELECT 'mensagens: '||count(*) FROM \"Message\" WHERE \"key\"->>'remoteJid'='${JID}';"
else
    docker exec evolution-postgres psql -U evolution -d evolution -c \
      "DELETE FROM \"Message\" WHERE \"key\"->>'remoteJid'='${JID}';
       DELETE FROM \"Chat\"    WHERE \"remoteJid\"='${JID}';
       DELETE FROM \"Contact\" WHERE \"remoteJid\"='${JID}';
       DELETE FROM \"IsOnWhatsapp\" WHERE \"jid\"='${JID}';"
fi

echo "✅ Concluído. (Guarda um registo deste pedido — é exigido pelo RGPD.)"
