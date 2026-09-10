FROM python:3.11-slim

WORKDIR /app

COPY agente/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código do agente
COPY agente/agente.py agente/config.py agente/traces.py agente/security.py agente/memory.py agente/relatorio.py ./

# Configuração e prompt (comportamento = dados, não código)
COPY configs ./configs
COPY prompts ./prompts

ENV AGENT_CONFIG=/app/configs/config.yaml
ENV AGENT_PROMPT=/app/prompts/system_prompt.md

CMD ["uvicorn", "agente:app", "--host", "0.0.0.0", "--port", "3000"]
