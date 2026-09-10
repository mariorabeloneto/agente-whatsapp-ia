# Decisões de arquitetura (ADR)

Registo dos compromissos técnicos que moldaram este projeto. Formato leve: o
contexto, a decisão, e o que se ganha/perde. Atualizar sempre que uma decisão
importante mudar.

---

## ADR-001 — Evolution API em vez da Cloud API oficial da Meta

**Contexto:** o agente precisa de ler e responder a mensagens de WhatsApp.

**Decisão:** usar a **Evolution API** (integração `WHATSAPP-BAILEYS`), que liga
por QR code a um número normal, em vez da **WhatsApp Cloud API** (oficial Meta).

**Porquê:**
- A Cloud API oficial exige verificação de empresa, um número dedicado e
  aprovação de templates de mensagem — meses de fricção para começar.
- A Evolution liga em minutos, suporta qualquer número, e corre self-hosted
  (os dados não passam por terceiros).

**Trade-off:** Baileys é uma integração não-oficial — há risco de bloqueio do
número pelo WhatsApp, sobretudo a partir de IPs de datacenter. Para produção a
escala, a Cloud API é o caminho correto. **Reavaliar quando houver cliente
pagante** — aí a autenticidade vale mais do que a velocidade de setup.

---

## ADR-002 — DeepSeek em vez de OpenAI/Anthropic

**Contexto:** o agente faz vários turnos de LLM por conversa; o custo por lead
importa para o modelo de negócio.

**Decisão:** **DeepSeek** (`deepseek-chat`) via API compatível com OpenAI.

**Porquê:**
- Custo por token muito inferior aos modelos de topo, com qualidade suficiente
  para uma conversa de vendas estruturada (não é raciocínio complexo).
- A API é OpenAI-compatible → trocar de fornecedor é mudar 3 variáveis de
  ambiente, não reescrever código.

**Trade-off:** depende de um fornecedor chinês (soberania de dados) e sem SLA
robusto. Mitigação: a interface é OpenAI-compatible, por isso a migração é
trivial. **O LLM deve ser configuração, não código** — ver Fase 2 do roadmap.

---

## ADR-003 — Handoff humano via Telegram, não pelo próprio WhatsApp

**Contexto:** quando um lead pede para falar com uma pessoa, o responsável
precisa de ser notificado e poder decidir se assume.

**Decisão:** notificar e perguntar ao responsável por **Telegram** (long-polling),
não por uma conversa de WhatsApp.

**Porquê:**
- O responsável não pode usar o mesmo número de WhatsApp que o agente (colisão).
- O Telegram dá um bot dedicado, com `getUpdates` (long-polling) — sem precisar
  de webhook público nem expor nada à internet.
- Permite o ciclo completo: perguntar SIM/NÃO, timeout de 30s, e comando
  `/retomar`.

**Trade-off:** mais uma dependência externa e mais um bot para gerir. Mas
resolve o problema sem expor endpoints públicos, o que era o objetivo.

---

## ADR-004 — Memória de conversa em processo (a corrigir)

**Contexto:** o histórico de cada conversa precisa de existir entre turnos.

**Decisão (atual):** guardar o histórico num `dict` em memória.

**Porquê:** simplicidade máxima na primeira versão.

**Trade-off:** um restart do container **perde todas as conversas a meio** e
impede correr mais do que uma réplica. É o limite mais sério do desenho atual.
**Plano:** mover para Postgres/Redis (já estão no stack para a Evolution).

---

## ADR-005 — Prompt e regras de negócio no código (a corrigir)

**Contexto:** cada cliente tem serviços, preços e tom diferentes.

**Decisão (atual):** o prompt vive numa string Python com lacunas `{{...}}`.

**Porquê:** zero infraestrutura; editar e correr.

**Trade-off:** "configurar" passa a ser editar código, e o segundo cliente
obriga a duplicar o ficheiro. **Plano:** extrair para `prompts/*.md` +
`configs/*.yaml`, para que o comportamento seja dados e o `agente.py` deixe de
saber quem é o cliente.

---

## O que faria diferente com mais tempo

- **Testes e evals desde o início.** Um agente que fala com clientes reais e
  escreve num CRM sem nenhum teste é frágil por construção.
- **Observabilidade primeiro.** Sem traces (tokens, custo, latência, taxa de
  agendamento) não há como provar que uma alteração ao prompt melhorou algo.
- **Guardrails à entrada.** O agente lê texto de estranhos e chama ferramentas
  que escrevem em sistemas reais — a superfície de prompt injection é real.
- **Estado persistente desde o turno zero**, em vez de em memória.
