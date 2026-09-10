#!/bin/bash
# monitor_lia.sh — verifica a saúde da stack e alerta por Telegram SÓ quando o
# estado muda (ok -> problema, problema -> ok). Corre no próprio servidor, por
# isso não depende de mais nenhuma máquina estar ligada.
#
# Instalado por deploy_prod.sh + systemd timer (lia-monitor.timer, a cada 15 min).

ENVDIR="/home/ubuntu/agente-whatsapp/agente"
STATE_FILE="/home/ubuntu/monitor_lia.state"
LOG="/home/ubuntu/monitor_lia.log"
APIKEY="minha-chave-secreta-123"

# token/chat do Telegram a partir do .env do agente
TG_TOKEN=$(grep -E '^TELEGRAM_TOKEN=' "$ENVDIR/.env" 2>/dev/null | cut -d= -f2-)
TG_CHAT=$(grep -E '^TELEGRAM_CHAT_ID=' "$ENVDIR/.env" 2>/dev/null | cut -d= -f2-)

PROBLEMAS=""

# 1. containers a correr
for c in agente evolution-api evolution-postgres evolution-redis; do
    st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)
    [ "$st" != "running" ] && PROBLEMAS="${PROBLEMAS}container $c=$st; "
done

# 2. saúde do agente (JSON doctor)
H=$(docker exec evolution-api wget -qO- --timeout=8 http://agente:3000/ 2>/dev/null)
echo "$H" | grep -q '"status":"ok"' || PROBLEMAS="${PROBLEMAS}agente_health=$(echo "$H" | head -c 80); "

# 3. ligação WhatsApp
W=$(curl -s --max-time 10 "http://localhost:8080/instance/connectionState/agente-mario" -H "apikey: $APIKEY")
echo "$W" | grep -q '"state":"open"' || PROBLEMAS="${PROBLEMAS}whatsapp=$(echo "$W" | head -c 80); "

# 4. pausa global (informativo — não é problema)
PAUSADO=$(docker exec evolution-redis redis-cli -n 7 EXISTS "agente:hist:flag:pausa_global" 2>/dev/null)

# estado atual
if [ -n "$PROBLEMAS" ]; then ESTADO="PROBLEMA"; else ESTADO="OK"; fi
ANTERIOR=$(cat "$STATE_FILE" 2>/dev/null || echo "DESCONHECIDO")
echo "$ESTADO" > "$STATE_FILE"
echo "$(date '+%F %T') estado=$ESTADO ${PROBLEMAS}" >> "$LOG"

enviar() {
    [ -z "$TG_TOKEN" ] && return
    curl -s --max-time 15 "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        -d "chat_id=${TG_CHAT}" --data-urlencode "text=$1" >/dev/null
}

# alerta só na transição
if [ "$ESTADO" = "PROBLEMA" ] && [ "$ANTERIOR" != "PROBLEMA" ]; then
    enviar "🔴 LIA — PROBLEMA detetado
${PROBLEMAS}

(servidor Oracle · $(date '+%d/%m %H:%M'))"
elif [ "$ESTADO" = "OK" ] && [ "$ANTERIOR" = "PROBLEMA" ]; then
    enviar "🟢 LIA — recuperou. Tudo a funcionar outra vez."
fi
