#!/bin/bash
# limpar_traces.sh — aplica a política de retenção aos traces (RGPD, limitação
# da conservação). Remove os registos com mais de RETENCAO_DIAS dias.
LOG="/home/ubuntu/monitor_lia.log"
RETENCAO_DIAS=90
echo "$(date '+%F %T') limpeza de traces (>${RETENCAO_DIAS}d)" >> "$LOG"
docker exec agente python relatorio.py --limpar "$RETENCAO_DIAS" >> "$LOG" 2>&1
