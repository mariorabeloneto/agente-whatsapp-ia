#!/bin/bash
# relatorio_lia.sh — gera o relatório de resultados (últimos 7 dias) e envia-o
# ao responsável por Telegram. Corre dentro do container do agente (tem acesso
# aos traces e às credenciais).
LOG="/home/ubuntu/monitor_lia.log"
echo "$(date '+%F %T') a gerar relatório" >> "$LOG"
docker exec agente python relatorio.py --dias 7 --telegram >> "$LOG" 2>&1
