# RGPD — enquadramento do agente WhatsApp

> ⚠️ **Isto não é aconselhamento jurídico.** É a análise técnica e operacional do
> sistema, com os pontos que um advogado deve validar antes de venderes o serviço.
> Serve para chegares à conversa com ele já com o trabalho feito.

---

## 1. Que dados pessoais são tratados (mapa)

| Dado | Onde fica | Retenção atual |
|---|---|---|
| Conteúdo das mensagens de WhatsApp + nº de telefone | Evolution API (Postgres, no servidor) | indefinida |
| Histórico recente da conversa (contexto do modelo) | Redis (servidor) | **7 dias** (configurável) |
| Excertos das mensagens + nº (observabilidade) | `traces/conversations.jsonl` | indefinida (ver §5) |
| Nome, empresa, email, telefone, necessidade, setor | Airtable (CRM) | indefinida |
| Nome, empresa, telefone, email (na descrição do evento) | Google Calendar | indefinida |
| Alertas de handoff (nome + nº) | Telegram | indefinida |
| Mensagens de cada turno (conteúdo) | **DeepSeek** (API do modelo) | conforme a política do fornecedor |
| Metadados técnicos | logs dos containers (servidor) | indefinida/truncada |

**Não são pedidos** (e o prompt deve continuar a não pedi-los): dados de saúde,
origem étnica, convicções, dados biométricos, financeiros. Se o cliente final os
escrever por iniciativa própria, aplica-se a minimização — não os guardes no CRM.

---

## 2. Papéis: quem é responsável por quê

Há **dois papéis distintos** e é importante não os confundir:

- **POST Group, em nome próprio** (prospeção dos *seus* leads): é **responsável
  pelo tratamento** (controller).
- **POST Group, a operar o agente para um cliente** (ex.: barbearia): o
  **cliente é o responsável** pelos dados dos clientes *dele*; a POST Group é
  **subcontratante** (processor). Isto **exige um contrato de subcontratação**
  (art. 28.º RGPD) com cada cliente, por escrito.

**Subcontratantes que usas (sub-processors)** — tens de os listar no contrato:
DeepSeek (modelo), Meta/WhatsApp (canal), Google (Calendar), Airtable (CRM),
Telegram (alertas), Oracle (alojamento).

---

## 3. Bases de licitude (art. 6.º)

- **Execução de contrato / diligências pré-contratuais** (6.º/1/b): responder a
  quem contacta a pedir informação/proposta.
- **Interesse legítimo** (6.º/1/f): prospeção comercial B2B, com direito de
  oposição claro.
- Para os **clientes de um cliente teu**, a base é definida **pelo cliente** (é
  ele o responsável). Tens de o deixar isso por escrito.

---

## 4. Transferências internacionais — o ponto mais crítico

- **DeepSeek**: o texto de cada conversa é enviado para servidores **na China**.
  **Não há decisão de adequação** da Comissão para a China. Servir um cliente
  europeu com isto exige, no mínimo, **Cláusulas Contratuais-Tipo (SCC)** +
  avaliação de impacto da transferência. **É o maior risco jurídico do projeto.**
  - **Opções:** (a) contrato com SCC aceites pelo fornecedor; ou (b) **mudar
    para um fornecedor com alojamento na UE** (ex.: um modelo europeu, ou um
    serviço com *data residency* na UE). A arquitetura já permite — o LLM é
    configuração (`DEEPSEEK_URL`), não código.
- **Google, Airtable, Telegram, Oracle**: verificar que estão cobertos pelo
  **EU-US Data Privacy Framework** ou por SCC. O alojamento (Oracle) está em
  **Frankfurt** — bem, os dados de armazenamento ficam na UE.

---

## 5. Retenção e minimização

- **Redis**: 7 dias (já configurado). ✅
- **Traces**: contêm excertos de conversa (dados pessoais). Agora há **rotação**:
  `python relatorio.py --limpar 90` apaga o que tiver mais de 90 dias.
  **Recomendação:** agendar isso (timer) e/ou ativar
  `TRACES_ANONIMIZAR=true`, que guarda só um hash do número e o comprimento do
  texto (sem conteúdo).
- **Airtable / Calendar / Evolution**: **sem prazo definido** hoje. Define uma
  política (ex.: 24 meses) e implementa-a. Enquanto não houver política, o
  simples facto de guardares indefinidamente já é um incumprimento do princípio
  da limitação da conservação.

---

## 6. Segurança (art. 32.º)

O que já está feito:
- Serviços internos não expostos: só a Evolution (8080) tem porta pública.
- Acesso por chave SSH; segredos em `.env` (fora do git).
- Guardrails contra prompt injection; limite de ações.

O que falta melhorar:
- **Encriptação do disco** do servidor (a Oracle suporta — ativar).
- Restringir a porta 8080 da Evolution ao teu IP ou pôr TLS à frente.
- Backups cifrados e testados.
- Ativar **MFA** nas contas Oracle, GitHub, Airtable, Google, Telegram.

---

## 7. Direitos dos titulares (e como os cumprir)

Acesso, retificação, apagamento, portabilidade, oposição. O sistema tem de
conseguir **responder em 1 mês**.

**Ferramenta de apagamento (art. 17.º)** — já existe:
```bash
# no servidor
~/apagar_dados.sh 351913326279           # apaga Redis + Airtable + Calendar + Evolution
~/apagar_dados.sh 351913326279 --dry     # só mostra o que apagaria
```
**Guarda um registo de cada pedido** (data, quem pediu, o que foi apagado) — o
RGPD exige que consigas demonstrar conformidade.

---

## 8. Ações antes do 1.º cliente pagante

1. **Resolver a transferência para a China**: SCCs com a DeepSeek **ou** migrar
   para um fornecedor com alojamento na UE. *(bloqueante)*
   → Procedimento pronto em **`docs/migracao-llm-runbook.md`** (Mistral/OVHcloud;
   o código já suporta — o fornecedor é configuração).
2. **Contrato de subcontratação (art. 28.º)** com cada cliente + lista de
   subcontratantes. *(bloqueante)*
3. **Aviso de privacidade** (ver `docs/aviso-privacidade.md`) acessível a quem
   fala com o agente. *(bloqueante)*
4. **Política de retenção** definida e a correr (Redis ✅, traces por agendar,
   CRM/Calendar/Evolution por definir).
5. **Registo das atividades de tratamento** (uma tabela simples — o §1 já é a
   base).
6. **Procedimento de pedidos de titulares** (usar `apagar_dados.sh`) + registo.
7. **MFA + encriptação do disco**.
8. **Avaliar DPIA**: para um serviço a pequena escala, provavelmente não é
   obrigatória — mas deve ser uma decisão registada, não um esquecimento.

---

## 9. Nota sobre AI Act

A assistente identifica-se como IA na primeira mensagem (art. 50.º, aplicável
desde 2 de agosto de 2026) — **isso já está implementado no prompt** e é o
requisito principal para este tipo de sistema. Mantém essa regra intacta.
