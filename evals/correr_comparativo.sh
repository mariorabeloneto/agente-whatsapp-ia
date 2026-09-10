#!/bin/bash
# correr_comparativo.sh — corre os evals em cada fornecedor com chave configurada
# e gera evals/comparativo.md.
#
# Uso:  ./evals/correr_comparativo.sh                 # 3 repetições por caso
#       REPETICOES=1 ./evals/correr_comparativo.sh    # corrida rápida
#       EVALS_ENV_FILE=~/lia-prod/agente/.env ./evals/correr_comparativo.sh
set -u
cd "$(dirname "$0")/.."

REPETICOES="${REPETICOES:-3}"
export EVALS_ENV_FILE="${EVALS_ENV_FILE:-$HOME/lia-github/agente/.env}"
echo "Fornecedores: os que tiverem chave em $EVALS_ENV_FILE (ou no ambiente)"
echo "Repetições por caso: $REPETICOES"
echo

for p in deepseek mistral ovhcloud; do
    echo "══════ $p ══════"
    python3 -m evals.run_evals --provider "$p" --repeticoes "$REPETICOES" \
        --out "evals/results-$p.json" && echo "  ✓ $p ok" || echo "  ⚠️ $p não correu (chave ausente? ver acima)"
    echo
done

echo "══════ comparativo ══════"
python3 -m evals.comparar
