# Agente WhatsApp com IA (FastAPI + Evolution API + DeepSeek)

Agente de atendimento e vendas para WhatsApp: qualificação de leads, agendamento
automático em Google Calendar, CRM em Airtable, alertas por Telegram e
**handoff bidirecional** para um humano assumir a conversa.

O comportamento é **dados, não código**: toda a configuração de um cliente vive
em `configs/config.yaml` e o prompt em `prompts/system_prompt.md`. O `agente.py`
não sabe quem é o cliente.

## Arquitetura

```
├── agente/
│   ├── agente.py          # loop/transporte (FastAPI) + integrações
│   ├── config.py          # carrega config.yaml + renderiza o prompt
│   ├── traces.py          # traces por conversa (tokens, custo, latência)
│   ├── security.py        # guardrails anti-injection
│   ├── Dockerfile · requirements.txt · .env.example
├── configs/
│   └── config.example.yaml  # copiar para config.yaml
├── prompts/
│   └── system_prompt.md     # prompt com lacunas {{...}}
├── evals/
│   ├── dataset.json · scorer.py · run_evals.py
├── tests/                 # pytest
├── evolution/docker-compose.yml
├── DECISIONS.md · LICENSE · start.sh · stop.sh
```

Stack Docker: **evolution-api** (gateway WhatsApp) + **postgres** + **redis** +
**agente** (FastAPI, só na rede interna).

## Funcionalidades

- Atendimento automático com prompt/config por cliente
- Identificação obrigatória do lead antes de avançar
- Qualificação com score
- Consulta da **agenda real** (Google Calendar) e proposta de **3 horários livres**
- Criação automática do evento (com Google Meet, se aplicável)
- Registo/atualização de leads no **Airtable** (upsert por telefone)
- **Handoff para humano** via Telegram (SIM/NÃO, timeout, `/retomar`)
- **Traces** por conversa: tokens, custo estimado, latência, ferramentas
- **Guardrails** anti-injection à entrada + limite de ações sensíveis
- **`/health`** tipo doctor: verifica Evolution, LLM, Calendar, Airtable, Telegram
- **Tests** (pytest) e **evals** com scorer

## Instalação

1. **Credenciais**
   ```bash
   cp agente/.env.example agente/.env     # editar com as tuas chaves
   ```

2. **Configuração do negócio**
   ```bash
   cp configs/config.example.yaml configs/config.yaml
   # editar config.yaml — identidade, serviços, preços, horários
   ```

3. **Prompt**
   Edita `prompts/system_prompt.md` se quiseres ajustar o comportamento.
   As lacunas `{{...}}` são preenchidas a partir do `config.yaml`.
   Para validar que não falta nada:
   ```bash
   python -c "from agente.config import *; print(missing_placeholders(load_config()))"
   ```

4. **Google Calendar (opcional)**: colocar o ficheiro de tokens no caminho
   definido em `GOOGLE_TOKENS_PATH` (ver `evolution/docker-compose.yml`).

5. **Subir o stack**
   ```bash
   cd evolution && docker compose up -d --build
   ```

6. **Ligar o WhatsApp** (criar instância + QR)
   ```bash
   curl -X POST "http://localhost:8080/instance/create" \
     -H "Content-Type: application/json" -H "apikey: <EVOLUTION_API_KEY>" \
     -d '{"instanceName":"meu-agente","integration":"WHATSAPP-BAILEYS","qrcode":true}'
   curl "http://localhost:8080/instance/connect/meu-agente" -H "apikey: <EVOLUTION_API_KEY>"
   ```

7. **Webhook**
   ```bash
   curl -X POST "http://localhost:8080/webhook/set/meu-agente" \
     -H "Content-Type: application/json" -H "apikey: <EVOLUTION_API_KEY>" \
     -d '{"webhook":{"enabled":true,"url":"http://agente:3000/webhook","events":["MESSAGES_UPSERT","CONNECTION_UPDATE","QRCODE_UPDATED"]}}'
   ```

## Qualidade (tests + evals)

```bash
pip install pytest pyyaml
python -m pytest tests/ -q          # testes unitários
python -m evals.run_evals --dry     # valida o dataset de evals
python -m evals.run_evals --provider deepseek   # corre os evals (chave em agente/.env)
```

Os evals escrevem `evals/results-<provider>.json` com antes/depois por caso,
latência e custo — é o número que responde a *"como sabes que isto funciona?"*.

### Comparar fornecedores de LLM (DeepSeek vs Mistral vs OVHcloud)

```bash
python -m evals.run_evals --provider deepseek
python -m evals.run_evals --provider mistral
python -m evals.comparar            # gera evals/comparativo.md (tabela + recomendação)
```

Trocar de fornecedor é configuração, não código: ver `agente/llm.py` e
`docs/fornecedores-llm.md`.

## Handoff humano

1. Lead pede humano → `quer_humano: true` no METADATA.
2. Agente pergunta ao responsável por Telegram se está disponível.
3. **SIM** → agente pausa nessa conversa; humano assume no WhatsApp.
4. **NÃO**/timeout (30s) → agente oferece 3 horários ao lead.
5. Humano a escrever no WhatsApp → agente deteta e pausa sozinho, guarda contexto.
6. `/retomar` ou `/retomar <telefone>` no Telegram → retoma.

> Usa um bot Telegram **próprio** — o long-polling exige acesso exclusivo.

## Segurança

- Nunca faz commit do `agente/.env` nem do `configs/config.yaml` (gitignored).
- Muda o `POSTGRES_PASSWORD` e a `EVOLUTION_API_KEY` dos defaults (`changeme`).
- `security.py` faz deteção heurística de injection — não é bala de prata;
  complementa-se com separação de papéis no prompt e limite de ações.
- Em produção, expõe a porta 8080 apenas a IPs de confiança ou atrás de TLS.

## Decisões

Ver [`DECISIONS.md`](DECISIONS.md) para os tradeoffs (Evolution vs Cloud API,
DeepSeek, Telegram para handoff) e o que está por melhorar.

## Licença

MIT — ver [`LICENSE`](LICENSE).
