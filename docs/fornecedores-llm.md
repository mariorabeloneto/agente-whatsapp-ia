# Fornecedores de LLM — trocar sem mexer no código

O agente fala com **qualquer API compatível com a OpenAI** (`POST {url}/chat/completions`).
O fornecedor ativo é resolvido por configuração em `agente/llm.py` e escolhido por
variáveis de ambiente. Mudar de fornecedor = mudar o `.env` e reiniciar o container.

## Configuração (`.env`)

```bash
LLM_PROVIDER=deepseek        # deepseek | mistral | ovhcloud
LLM_API_KEY=...              # chave genérica; alternativa: a env própria do fornecedor
#LLM_MODEL=mistral-small-latest   # opcional (sobrepõe o default do fornecedor)
#LLM_URL=https://api.mistral.ai/v1
PRICE_IN_PER_M=0.10          # opcional — só para estimativa de custo
PRICE_OUT_PER_M=0.30
```

A chave também pode vir na env específica: `DEEPSEEK_API_KEY`, `MISTRAL_API_KEY`,
`OVHCLOUD_API_KEY`. Nunca é impressa em logs (`LLM.resumo()` não a inclui) e o
`/health` devolve qual o fornecedor ativo.

## Fornecedores suportados

| ID | Modelo por omissão | Onde corre | Enquadramento RGPD |
|---|---|---|---|
| `deepseek` | `deepseek-chat` | China | **Sem** decisão de adequação da UE → transferência exige SCC + avaliação de risco |
| `mistral` | `mistral-small-latest` | França (UE) | Tratamento na UE; DPA no site da Mistral |
| `ovhcloud` | `Mistral-Small-3.2-24B-Instruct-2506` | UE (OVHcloud, França) | Inferência em solo UE |

Adicionar um fornecedor novo = acrescentar uma entrada em `PROVIDERS` (`agente/llm.py`).
Testes: `tests/test_llm.py`.

## Teste comparativo (decidir com números)

```bash
python -m evals.run_evals --provider deepseek      # baseline (o que está em produção)
python -m evals.run_evals --provider mistral       # candidato
python -m evals.run_evals --provider ovhcloud      # candidato
python -m evals.comparar                           # → evals/comparativo.md
```

Um fornecedor por processo (a config do agente resolve-se no import). O runner
mede, por caso: **qualidade** (dataset de 11 casos em `evals/dataset.json`),
**latência** (por turno, com p50/p95), **tokens** e **custo estimado**
(tokens × `PRICE_*_PER_M`).

O modelo varia entre corridas no mesmo fornecedor, por isso o comparativo corre
com **3 repetições** (`--repeticoes 3`, ~3 min por fornecedor) e reporta a
estabilidade de cada caso. Sem repetições, uma diferença de 1 caso não significa
nada.

### Onde põem as chaves (local, nunca a commit)

O runner lê `agente/.env` (gitignored) por omissão; `EVALS_ENV_FILE` aponta para
outro ficheiro — por exemplo o `.env` real de produção:

```bash
EVALS_ENV_FILE=~/lia-prod/agente/.env python -m evals.run_evals --provider deepseek
```

Para testar candidatos, o mais simples é um ficheiro só para as chaves novas:

```bash
echo 'MISTRAL_API_KEY=a-tua-chave' > ~/lia-github/agente/.env   # gitignored
python -m evals.run_evals --provider mistral
```

### Critérios de aceitação (definidos a priori)

1. **Qualidade** ≥ baseline − 1 caso (11 casos; perda maior exige re-afinar o prompt e repetir).
2. **Latência** p50 sem agravamento > 50%.
3. **Dados em solo UE** (argumento comercial e de RGPD).

O `comparar.py` aplica estes critérios automaticamente e escreve a recomendação.
Os preços são configuráveis e **têm de ser confirmados** na página oficial do
fornecedor antes de uma decisão por custo.

## Migrar em produção

1. Correr os evals nos candidatos e ler `evals/comparativo.md`.
2. Decidir e, se aplicável, criar a conta/chave no fornecedor escolhido e assinar o **DPA**.
3. No servidor: `~/agente-whatsapp/agente/.env` → `LLM_PROVIDER` + chave; `docker compose up -d agente`.
4. Verificar `curl http://localhost:8080/...` e o `/health` (`fornecedor_llm.id` deve mudar).
5. Repetir os evals **contra o mesmo código** e comparar com o baseline.
6. Actualizar `docs/RGPD.md` (subcontratantes e transferências) e o aviso de privacidade.

### Rollback

`LLM_PROVIDER=deepseek` no `.env` + `docker compose up -d agente`. Sem alterações de código,
sem migração de dados: o histórico está no Redis e não depende do fornecedor.
