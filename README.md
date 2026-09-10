# Agente WhatsApp com IA (FastAPI + Evolution API + DeepSeek)

Agente de atendimento e vendas para WhatsApp, escrito em Python (FastAPI), com
qualificação de leads, agendamento automático em Google Calendar, CRM em Airtable,
alertas por Telegram e **handoff bidirecional** para um humano assumir a conversa.

Este repositório é um **template genérico**. O comportamento do agente é definido
por um único prompt (o `SYSTEM_PROMPT` em `agente/agente.py`), que traz **lacunas
`{{...}}` para preencher** com os dados do teu negócio.

## Arquitetura

```
├── agente/
│   ├── agente.py          # Toda a lógica (FastAPI, prompt, integrações)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example       # Modelo de credenciais (copiar para .env)
├── evolution/
│   └── docker-compose.yml # Evolution API + Postgres + Redis + agente
├── start.sh / stop.sh     # Arranque/paragem do stack
└── .gitignore
```

O stack corre com 4 containers:
- **evolution-api** — gateway de ligação ao WhatsApp (Baileys)
- **postgres** / **redis** — base de dados e cache da Evolution
- **agente** — o teu agente FastAPI (só acessível na rede interna)

## Funcionalidades

- Atendimento automático com prompt configurável (tom, fases, regras de negócio)
- Identificação obrigatória do lead (nome + negócio) antes de avançar
- Qualificação com score e classificação de estado do lead
- Consulta da **agenda real** (Google Calendar) e proposta de **3 horários livres**
- Criação automática do evento (com Google Meet, se aplicável)
- Registo/atualização de leads no **Airtable** (upsert por telefone)
- **Handoff para humano**: o agente pergunta ao responsável (Telegram) se está
  disponível; se sim, o humano assume a conversa no WhatsApp e o agente cala-se
  (mas guarda todo o contexto); se não, oferece reagendamento
- Deteção de mensagens multimédia e resposta padrão

## Pré-requisitos

- Docker + Docker Compose
- Uma chave de API de um LLM compatível com OpenAI (ex.: DeepSeek)
- (Opcional) Conta Airtable, bot Telegram, credenciais Google Calendar

## Instalação

1. **Clona e configura as credenciais**
   ```bash
   cp agente/.env.example agente/.env
   # edita agente/.env com as tuas chaves
   ```

2. **Preenche o prompt**
   Abre `agente/agente.py` e substitui todas as lacunas `{{...}}` do
   `SYSTEM_PROMPT` pelos dados do teu negócio (nome da empresa, serviços,
   preços, etc.).

3. **Google Calendar (opcional)**
   Coloca o ficheiro de tokens OAuth no caminho definido em
   `GOOGLE_TOKENS_PATH` (ver `evolution/docker-compose.yml`) antes de subir.

4. **Sobe o stack**
   ```bash
   cd evolution
   docker compose up -d --build
   ```

5. **Liga o WhatsApp**
   Cria a instância na Evolution e lê o QR code:
   ```bash
   curl -X POST "http://localhost:8080/instance/create" \
     -H "Content-Type: application/json" \
     -H "apikey: <a-tua-EVOLUTION_API_KEY>" \
     -d '{"instanceName":"meu-agente","integration":"WHATSAPP-BAILEYS","qrcode":true}'

   # obter o QR (base64) para escanear no telemóvel
   curl "http://localhost:8080/instance/connect/meu-agente" -H "apikey: <a-tua-key>"
   ```

6. **Configura o webhook** (para o agente receber as mensagens)
   ```bash
   curl -X POST "http://localhost:8080/webhook/set/meu-agente" \
     -H "Content-Type: application/json" -H "apikey: <a-tua-key>" \
     -d '{"webhook":{"enabled":true,"url":"http://agente:3000/webhook","events":["MESSAGES_UPSERT","CONNECTION_UPDATE","QRCODE_UPDATED"]}}'
   ```

## Handoff humano (como funciona)

1. O lead pede para falar com uma pessoa → o agente marca `quer_humano: true`.
2. O agente envia um Telegram ao responsável a perguntar se está disponível.
3. **Responde SIM** → o agente cala-se nessa conversa e o humano assume no WhatsApp.
4. **Responde NÃO** (ou não responde em 30s) → o agente oferece 3 horários ao lead.
5. Se o **humano escrever directamente** no WhatsApp, o agente deteta e pausa-se
   automaticamente, mantendo o contexto.
6. Para retomar: comando `/retomar` (todas) ou `/retomar <telefone>` no Telegram.

> Usa um **bot Telegram próprio** para este agente — o long-polling requer acesso
> exclusivo ao bot.

## Segurança

- **Nunca** faças commit do `agente/.env` (já está no `.gitignore`).
- Usa chaves fortes e distintas para a Evolution API.
- Em produção, expõe a porta da Evolution API apenas a IPs de confiança ou
  coloca-a atrás de um reverse proxy com TLS.

## Licença

MIT (ou a que preferires).
