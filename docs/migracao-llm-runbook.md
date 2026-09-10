# Runbook — migrar o LLM para um fornecedor com dados na UE

> **Quando usar:** quando existir um **cliente real** (pagante) e for preciso
> resolver a transferência de dados para fora da UE. **Não** é para fazer "por
> fazer": a produção fica na DeepSeek até esse momento.
>
> Estado atual: **produção = DeepSeek**. Candidato escolhido: **Mistral**
> (`api.eu.mistral.ai`, endpoint regional UE). Alternativa mais barata:
> **OVHcloud AI Endpoints**.
>
> O código **já está pronto** — o fornecedor é configuração (`agente/llm.py`).
> Ver também `docs/fornecedores-llm.md`.

---

## 0. Pré-requisitos (fazer ANTES de mexer em produção)

1. **Conta Mistral** em `console.mistral.ai` (o console de programadores — **não**
   é o `chat.mistral.ai`).
2. **Ativar faturação** em `console.mistral.ai/billing` → adicionar método de
   pagamento (**mínimo de carregamento ~10 €**, é saldo pré-pago, não mensalidade).
   - ⚠️ **NÃO** subscrever "Le Chat Pro" (€14,75/mês) — é o chatbot e **não dá
     créditos de API**. São produtos separados.
   - ⚠️ **NÃO** usar o plano gratuito "Experiment": obriga a aceitar que os dados
     sejam usados para **treino**.
3. **Confirmar o limite**: com a chave, `curl https://api.mistral.ai/v1/models`
   deve dar 200 e deixar de responder `429` com `x-ratelimit-limit-req-minute: 0`.
4. **Assinar o DPA** da Mistral (acordo de subcontratação) e guardá-lo.
5. **Regenerar a chave** (a que foi partilhada em chat deve considerar-se
   comprometida) e guardá-la só no `.env`.

---

## 1. Medir antes de decidir (mesmo antes de tocar em produção)

```bash
cd ~/lia-github

# ficheiro com as chaves (fora do repo)
cat > ~/.evals-env <<'EOF'
DEEPSEEK_API_KEY=...      # a de produção
MISTRAL_API_KEY=...
EOF
chmod 600 ~/.evals-env

# comparar com o prompt REAL (não o template do repo)
REPETICOES=3 \
EVALS_ENV_FILE=~/.evals-env \
AGENT_CONFIG=~/lia-prod/configs/config.yaml \
AGENT_PROMPT=~/lia-prod/prompts/system_prompt.md \
./evals/correr_comparativo.sh
```

Resultado: `evals/comparativo.md` (qualidade, latência p50/p95, custo/caso).
**Critério de decisão:** o candidato UE tem de fazer **o mesmo 11/11** do baseline;
se falhar casos ou a latência disparar, ajusta-se o prompt antes de migrar.

---

## 2. Migrar (produção)

Editar **`~/lia-prod/evolution/.env`** e acrescentar:

```bash
LLM_PROVIDER=mistral
LLM_API_KEY=<chave nova>
# endpoint regional UE (dados na UE/EFTA):
LLM_URL=https://api.eu.mistral.ai/v1
LLM_MODEL=mistral-small-latest
PRICE_IN_PER_M=0.10
PRICE_OUT_PER_M=0.30
```

Depois:

```bash
cd ~/lia-prod && ./deploy_prod.sh
```

---

## 3. Validar

```bash
ssh oracle-lia

# /health deve mostrar o fornecedor ativo e tudo "ok"
docker exec evolution-api wget -qO- http://agente:3000/ | head -c 300

# uma conversa real de teste (de outro telemóvel) → confirmar resposta
docker logs agente --tail 20
```

- [ ] `health: ok`
- [ ] WhatsApp `open`
- [ ] Conversa real responde normalmente
- [ ] Trace gravado com latência e custo do novo fornecedor

---

## 4. Rollback (se algo correr mal)

Basta reverter o `.env` para o fornecedor anterior e redeployar:

```bash
# remover/Comentar as linhas LLM_* adicionadas → volta ao default deepseek
cd ~/lia-prod && ./deploy_prod.sh
```
O `deploy_prod.sh` faz backup antes de instalar; também há
`~/backup_agente_<stamp>.tar.gz` no servidor.

---

## 5. Depois de migrar (não esquecer)

- [ ] Atualizar **`docs/RGPD.md`** §4 (transferências) e §2 (subcontratantes):
      deixar de haver transferência para a China.
- [ ] Atualizar **`docs/aviso-privacidade.md`**: o fornecedor de IA passa a ser
      europeu (melhora o texto).
- [ ] Guardar o **DPA** assinado; acrescentar ao registo de tratamentos.
- [ ] Referir no contrato com o cliente a lista de subcontratantes atualizada.
- [ ] Manter a **revisão jurídica** do texto final por um advogado.

---

## Notas

- **Custo esperado:** ~2–3 cêntimos por conversa completa. Os 10 € de saldo dão
  para 300–400 conversas (muito mais, se for OVHcloud).
- **Um fornecedor por processo:** o agente resolve a config no arranque, por isso
  trocar exige reiniciar o container (o deploy faz isso).
- **A chave nunca aparece em logs** — `LLM.resumo()` e o `/health` não a incluem.
