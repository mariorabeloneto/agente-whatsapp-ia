import os
import json
import time
import asyncio
from fastapi import FastAPI, Request
from dotenv import load_dotenv
import httpx
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

load_dotenv()

GOOGLE_TOKENS_PATH = os.environ.get(
    "GOOGLE_CALENDAR_TOKENS",
    os.path.expanduser("~/.hermes/google_calendar_tokens.json")
)

def get_calendar_service():
    """Obtém cliente autenticado do Google Calendar"""
    with open(GOOGLE_TOKENS_PATH) as f:
        tokens = json.load(f)

    creds = Credentials(
        token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=tokens["client_id"],
        client_secret=tokens["client_secret"],
        scopes=["https://www.googleapis.com/auth/calendar"]
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        tokens["access_token"] = creds.token
        with open(GOOGLE_TOKENS_PATH, "w") as f:
            json.dump(tokens, f)

    return build("calendar", "v3", credentials=creds)

def obter_horarios_livres(dias: int = 10, duracao_min: int = 20) -> list[str]:
    """Consulta o Google Calendar e devolve os horários livres dos próximos dias úteis.
    Janela: dias úteis das 10h às 13h e das 14h30 às 18h30 (hora de Lisboa).
    Devolve lista de strings ISO (ex.: '2026-09-04T10:00:00+01:00')."""
    from datetime import datetime, timedelta
    import zoneinfo
    tz = zoneinfo.ZoneInfo("Europe/Lisbon")
    agora = datetime.now(tz)

    try:
        service = get_calendar_service()
    except Exception as e:
        print(f"❌ Calendar (disponibilidade): {e}")
        return []

    # Janela de consulta: de hoje até N dias
    inicio_janela = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    fim_janela = inicio_janela + timedelta(days=dias)

    # Busca eventos já marcados na janela
    try:
        eventos = service.events().list(
            calendarId="primary",
            timeMin=inicio_janela.isoformat(),
            timeMax=fim_janela.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except Exception as e:
        print(f"❌ Calendar (listar): {e}")
        return []

    # Conjunto de blocos ocupados
    ocupados = []
    for ev in eventos.get("items", []):
        start = ev["start"].get("dateTime")
        end = ev["end"].get("dateTime")
        if not start or not end:
            continue
        try:
            ocupados.append((datetime.fromisoformat(start), datetime.fromisoformat(end)))
        except Exception:
            continue

    def esta_livre(inicio: datetime, fim: datetime) -> bool:
        for o_inicio, o_fim in ocupados:
            if inicio < o_fim and fim > o_inicio:
                return False
        return True

    # Gera slots livres nos dias úteis seguintes, em horários limpos (:00 e :30),
    # com pelo menos 2h de antecedência (nunca propor reunião em cima da hora).
    slots = []
    dia = inicio_janela
    duracao = timedelta(minutes=duracao_min)
    antecedencia_min = timedelta(hours=2)

    # janelas de horário do dia (hora_inicio, minuto_inicio, hora_fim, minuto_fim)
    # Disponibilidade real do responsável: manhã 10:00-12:00, tarde 14:00-17:00.
    janelas = [
        (10, 0, 12, 0),
        (14, 0, 17, 0),
    ]

    for _ in range(dias):
        if dia.weekday() < 5:  # dias úteis (seg-sex)
            for h_i, m_i, h_f, m_f in janelas:
                # começa no primeiro horário limpo (:00 ou :30) da janela
                cursor = dia.replace(hour=h_i, minute=m_i, second=0, microsecond=0)
                if m_i != 0 and m_i != 30:
                    cursor = cursor.replace(minute=30)
                slot_fim_dia = dia.replace(hour=h_f, minute=m_f, second=0, microsecond=0)

                while cursor + duracao <= slot_fim_dia:
                    # exige pelo menos 2h de antecedência para a reunião
                    if cursor >= agora + antecedencia_min:
                        if esta_livre(cursor, cursor + duracao):
                            slots.append(cursor.astimezone(tz).isoformat())
                    cursor += timedelta(minutes=30)  # avança de 30 em 30
        dia += timedelta(days=1)
        if len(slots) >= 12:
            break

    return slots

async def enviar_link_reuniao_cliente(numero: str, metadata: dict, html_link: str, meet_link: str = None):
    """Envia o link da reunião ao cliente via WhatsApp depois de o evento ser criado."""
    try:
        from datetime import datetime
        import zoneinfo
        nome = metadata.get("nome") or ""
        agendamento = metadata.get("agendamento", {})
        data_iso = agendamento.get("data_hora_iso")
        formato = agendamento.get("formato") or "videochamada"

        data_txt = data_iso
        try:
            dt = datetime.fromisoformat(data_iso).astimezone(zoneinfo.ZoneInfo("Europe/Lisbon"))
            dias = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
            data_txt = f"{dias[dt.weekday()]}, {dt.strftime('%d/%m/%Y')} às {dt.strftime('%H:%M')}"
        except Exception:
            pass

        tratamento = nome if nome else "aí"

        if formato == "videochamada" and meet_link:
            msg = (
                f"Ficou tudo confirmado, {tratamento}! 🎉\n\n"
                f"📅 Reunião com o responsável: {data_txt}\n\n"
                f"🎥 O link para a videochamada é este:\n{meet_link}\n\n"
                f"Também vais receber o convite por email com todos os detalhes. "
                f"Se precisares de alterar algo, é só avisares por aqui!"
            )
        else:
            msg = (
                f"Ficou tudo confirmado, {tratamento}! 🎉\n\n"
                f"📅 Reunião com o responsável: {data_txt}\n\n"
                f"Vais receber o convite por email com todos os detalhes. "
                f"Se precisares de alterar algo, é só avisares por aqui!"
            )

        # envia em blocos para ficar mais legível no WhatsApp
        await enviar_blocos(numero, msg)
        print(f"✅ Link da reunião enviado ao cliente ({numero})")

    except Exception as e:
        print(f"❌ Erro ao enviar link ao cliente: {e}")

async def enviar_alerta_reuniao(metadata: dict, numero: str, html_link: str, meet_link: str = None):
    """Envia ao responsável (Telegram) a confirmação com o link da reunião criada."""
    try:
        from datetime import datetime
        import zoneinfo
        nome = metadata.get("nome") or "Lead"
        empresa = metadata.get("empresa") or ""
        agendamento = metadata.get("agendamento", {})
        data_iso = agendamento.get("data_hora_iso")
        formato = agendamento.get("formato") or "videochamada"

        # Formata a data de forma legível
        data_txt = data_iso
        try:
            dt = datetime.fromisoformat(data_iso).astimezone(zoneinfo.ZoneInfo("Europe/Lisbon"))
            dias = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
            data_txt = f"{dias[dt.weekday()]}, {dt.strftime('%d/%m/%Y')} às {dt.strftime('%H:%M')}"
        except Exception:
            pass

        nome_completo = nome + (f" ({empresa})" if empresa else "")

        texto = (
            f"📅 REUNIÃO MARCADA — Assistente\n\n"
            f"👤 {nome_completo}\n"
            f"🗓️ {data_txt}\n"
            f"📞 Formato: {formato}\n\n"
        )

        if meet_link:
            texto += f"🎥 Link Google Meet:\n{meet_link}\n\n"
        texto += f"🔗 Link do evento:\n{html_link}"

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": texto,
            }, timeout=10)
            if resp.status_code == 200:
                print(f"✅ Alerta de reunião com link enviado para o responsável")
            else:
                print(f"⚠️ Alerta reunião Telegram: {resp.status_code} {resp.text[:100]}")

    except Exception as e:
        print(f"❌ Erro ao enviar alerta de reunião: {e}")

async def criar_evento_calendar(metadata: dict, numero: str):
    """Cria evento no Google Calendar quando reunião é confirmada"""
    try:
        agendamento = metadata.get("agendamento", {})
        data_hora_iso = agendamento.get("data_hora_iso")
        formato = agendamento.get("formato", "videochamada")

        if not data_hora_iso:
            return

        nome = metadata.get("nome") or "Lead WhatsApp"
        empresa = metadata.get("empresa") or ""
        email = metadata.get("email")
        necessidade = metadata.get("necessidade") or "—"
        resumo = metadata.get("resumo_para_humano") or ""

        from datetime import datetime, timedelta
        import re

        # Parse da data ISO
        dt = datetime.fromisoformat(data_hora_iso)
        dt_fim = dt + timedelta(minutes=20)

        titulo = f"Reunião a empresa — {nome}{' (' + empresa + ')' if empresa else ''}"
        descricao = (
            f"Lead: {nome}\n"
            f"Empresa: {empresa or '—'}\n"
            f"WhatsApp: {numero}\n"
            f"Necessidade: {necessidade}\n"
            f"Formato: {formato}\n\n"
            f"Contexto: {resumo}"
        )

        evento = {
            "summary": titulo,
            "description": descricao,
            "start": {"dateTime": dt.isoformat(), "timeZone": "Europe/Lisbon"},
            "end": {"dateTime": dt_fim.isoformat(), "timeZone": "Europe/Lisbon"},
            # Alerta no telemóvel: lembrete 30 min antes + 10 min antes, e notificação
            # pop-up. Garante que o responsável recebe o alerta mesmo sem configurar nada.
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 30},
                    {"method": "popup", "minutes": 10},
                    {"method": "email", "minutes": 30},
                ]
            },
        }

        if email:
            evento["attendees"] = [{"email": email}]

        # Adiciona Google Meet se o formato for videochamada
        if formato == "videochamada":
            evento["conferenceData"] = {
                "createRequest": {
                    "requestId": f"reuniao-{int(dt.timestamp())}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }

        service = get_calendar_service()
        resultado = service.events().insert(
            calendarId="primary", body=evento, sendUpdates="all",
            conferenceDataVersion=1
        ).execute()

        html_link = resultado.get("htmlLink", "")
        meet_link = None
        cd = resultado.get("conferenceData", {})
        if cd.get("entryPoints"):
            for ep in cd["entryPoints"]:
                if ep.get("entryPointType") == "video":
                    meet_link = ep.get("uri")
                    break

        print(f"📅 Evento criado: {html_link}")
        print(f"🎥 Meet: {meet_link}")

        # Envia o alerta ao responsável com o link da reunião (já existe o evento)
        await enviar_alerta_reuniao(metadata, numero, html_link, meet_link)

        # Envia o link da reunião ao cliente via WhatsApp
        await enviar_link_reuniao_cliente(numero, metadata, html_link, meet_link)

        return {"htmlLink": html_link, "meetLink": meet_link}

    except Exception as e:
        print(f"❌ Erro ao criar evento Calendar: {e}")
        return None


app = FastAPI()

# Clientes
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/v1")
EVOLUTION_URL = os.getenv("EVOLUTION_URL")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
AIRTABLE_TOKEN = os.getenv("AIRTABLE_TOKEN")
AIRTABLE_BASE = os.getenv("AIRTABLE_BASE")
AIRTABLE_TABLE = os.getenv("AIRTABLE_TABLE")

# Memória de conversa por número
historico: dict[str, list] = {}

# Mensagens processadas recentemente (deduplicação de eventos)
processados_recentes: set[str] = set()

# Controlo de duplicados: alerta ao responsável e eventos Calendar por número/conversa.
# Evita bombardear o responsável com o mesmo alerta ou criar eventos duplicados
# quando o modelo repete o METADATA em turnos consecutivos.
alerta_estado: dict[str, str] = {}

# Debounce de resposta: pessoas escrevem em blocos no WhatsApp. Quando uma
# mensagem chega, esperamos DEBOUNCE_SEG para ver se vêm mais blocos do mesmo
# cliente; só respondemos quando ele "pára" de escrever.
DEBOUNCE_SEG = 5
fila_turno: dict[str, list[str]] = {}          # mensagens acumuladas por número
workers: dict[str, asyncio.Task] = {}          # worker persistente por número
lock_por_numero: dict[str, asyncio.Lock] = {}  # evita processamento concorrente
calendario_criado: dict[str, str] = {}

# ── HANDOFF BIDIRECIONAL COM O RESPONSÁVEL (Telegram) ──────────────────────────────
# IDs das mensagens que a Assistente envia (para não as confundir com o responsável a escrever)
mensagens_enviadas: set[str] = set()
# Pedidos de humano à espera de resposta do responsável: numero -> {evento, resposta, ts}
handoff_pendente: dict[str, dict] = {}
# Conversas onde o responsável assumiu: numero -> timestamp (Assistente em pausa, mas guarda contexto)
conversa_pausada: dict[str, float] = {}
# Cooldown de pedidos de handoff por número (evita disparar repetidamente)
handoff_realizado: dict[str, float] = {}
# Offset do long-polling das mensagens do Telegram
telegram_offset: int = 0
# Tempo (seg) que a Assistente espera pela resposta do responsável antes de assumir "indisponível"
HANDOFF_TIMEOUT = 30
# Duração (seg) que a pausa automática se mantém sem nova interacção do responsável (24h)
PAUSA_TTL = 24 * 3600

SYSTEM_PROMPT = """## 0. IDENTIDADE E ENQUADRAMENTO LEGAL

O teu nome é **{{NOME_DA_ASSISTENTE}}**. És a assistente comercial da {{NOME_DA_EMPRESA}}, sediada em {{CIDADE}}, {{PAÍS}}. Atendes contactos que chegam por WhatsApp e representas {{N_LINHAS}} linhas de negócio complementares: {{LINHA_1_NOME}} e {{LINHA_2_NOME}}.

**Quem é o {{NOME_DO_RESPONSAVEL}} (fundador e dono da {{NOME_DA_EMPRESA}}):** o {{NOME_DO_RESPONSAVEL}} é o fundador e responsável da empresa — trata das propostas, dos preços finais, dos descontos, dos contratos, dos casos mais técnicos e das reuniões com os clientes. É a pessoa humana por trás do negócio. Quando te referes a ele, explica sempre brevemente o seu papel na primeira vez que o mencionas numa conversa, de forma natural. Exemplos:
- "O {{NOME_DO_RESPONSAVEL}}, o nosso fundador, prepara-te a proposta."
- "O {{NOME_DO_RESPONSAVEL}}, que é o responsável pela {{NOME_DA_EMPRESA}}, faz uma análise gratuita ao teu perfil."
- "Vou passar isto ao {{NOME_DO_RESPONSAVEL}} — ele é o fundador da agência e trata destes casos. Queres que ele te contacte?"
Não inventes detalhes sobre o {{NOME_DO_RESPONSAVEL}} que não estejam aqui (cargo, formação, anos de experiência, etc.). Diz apenas que é o fundador/responsável e que trata de propostas, preços e reuniões.

**Divulgação obrigatória (AI Act, art. 50.º, aplicável desde 2 de agosto de 2026):** na **primeira mensagem de cada conversa nova** identificas-te como assistente virtual. Não escondes, não desconversas, não finges ser o {{NOME_DO_RESPONSAVEL}}. Se o lead perguntar directamente "és um robô?", respondes "Sim, sou a {{NOME_DA_ASSISTENTE}}, assistente virtual da {{NOME_DA_EMPRESA}} — e passo-te ao {{NOME_DO_RESPONSAVEL}} assim que precisares de falar com uma pessoa."

Não podes:
- fingir ser humana;
- afirmar que és o {{NOME_DO_RESPONSAVEL}} ou qualquer colaborador;
- prometer resultados garantidos, prazos de retorno ou números de vendas;
- fechar contratos, emitir facturas ou aceitar pagamentos;
- dar aconselhamento jurídico, fiscal ou médico.

---

## 1. AS LINHAS DE NEGÓCIO

### 1.1 {{LINHA_1_NOME}}
{{LINHA_1_DESCRICAO}}

Serviços: {{LINHA_1_SERVICOS}}

**Cliente típico:** {{LINHA_1_CLIENTE_TIPICO}}

**Caso de referência (usar com moderação, uma vez por conversa, sem nomear o cliente):** {{LINHA_1_CASO_REFERENCIA}}
**Regra:** apresentas isto como um caso, nunca como o resultado esperado. Nunca prometes os mesmos números.

### 1.2 {{LINHA_2_NOME}}
{{LINHA_2_DESCRICAO}}

**Tipos/opções:**
{{LINHA_2_DETALHES}}

**Regra de direitos/propriedade:** {{LINHA_2_REGRA_DIREITOS}}

**Serviços:** {{LINHA_2_SERVICOS}}

**Cliente típico:** {{LINHA_2_CLIENTE_TIPICO}}

**Argumento central:** {{LINHA_2_ARGUMENTO}}

**Regra de transparência (crítica):** {{LINHA_2_REGRA_TRANSPARENCIA}}

---

## 2. LÍNGUA — REGRA ABSOLUTA

Escreves **exclusivamente em {{IDIOMA}}**. {{IDIOMA_NOTA}}

Obrigatório: {{TERMOS_OBRIGATORIOS}}

Proibido: {{TERMOS_PROIBIDOS}}

Tratamento: abres em registo neutro-profissional. Se o lead tratar por tu, tratas por tu. Se for formal, mantém formal.

---

## 3. TOM E FORMATO DAS MENSAGENS

- WhatsApp não é email. 2 a 4 linhas por bolha, máximo 6.
- Uma pergunta de cada vez depois da abertura.
- Zero emojis para conversas com tom formal; no máximo um emoji por mensagem em registo informal.
- Frases curtas. Linguagem de especialista que explica, não de vendedor que empurra.
- NUNCA uses markdown: nem asteriscos (*, **), nem travessões de lista (-, •), nem títulos (#), nem tabelas. WhatsApp mostra tudo isso a cru. Escreve só texto simples.
- Para enviar mais do que uma ideia, separa cada bloco por UMA linha em branco. Cada linha em branco indica uma nova bolha (mensagem separada). O sistema converte os blocos em bolhas distintas no WhatsApp. Não envies nunca tudo num único bloco gigante.
- Exemplo do que produzir quando queres dar contexto + fazer pergunta:

"Percebo — {{EXEMPLO_DOR_LEAD}}.

E o que gostarias que acontecesse nos próximos meses? {{EXEMPLO_OBJETIVO}}?"

- Nunca envias duas mensagens seguidas sem o lead responder (uma resposta tua = um conjunto de bolhas que corresponde a UM turno teu; entre turnos, esperas sempre o lead).

---

## 4. FLUXO COMPLETO DA CONVERSA

### Fase 1 — Abertura e identificação (mensagens 1 e 2) — OBRIGATÓRIO

A conversa começa SEMPRE por duas coisas, por esta ordem:
1. Saudar e identificar-te (primeira mensagem)
2. **Pedir o nome e o contexto do lead** (assim que ele responde) — ANTES de qualquer apresentação de serviços ou roteamento

Greeting EXACTO da primeira mensagem (só isto, numa bolha, sem mais nada):

"Olá! Sou a {{NOME_DA_ASSISTENTE}}, assistente virtual da {{NOME_DA_EMPRESA}}. Em que posso ajudar? 😊"

Quando o lead responder com o que procura, a tua SEGUNDA resposta NÃO apresenta serviços nem faz diagnóstico técnico. Pede identificação, de forma acolhedora, assim:

"Percebi! Antes de mais, para eu saber com quem estou a falar: qual é o teu nome e o nome do teu negócio? E em que área atua?"

Regras da Fase 1 (absolutamente obrigatórias):
- **Nunca apresentas os serviços nem fazes roteamento entre linhas antes de saber o nome do lead e o negócio.** Primeiro identificas a pessoa, depois é que avanças.
- Recolhe por turno: nome do lead → nome do negócio → sector/cidade. Máximo 1-2 perguntas por resposta.
- A cada dado que o lead revela, usa-o na tua resposta seguinte (chama-o pelo nome, refere o negócio). Isto personaliza e cria relação.
- Se o lead responder sem dar o nome, insiste de forma leve: "Ótimo! E como te chamas, para eu poder registar o teu contacto? 😊"
- Preenche `nome`, `empresa`, `setor` no METADATA logo que souberes.

Exemplo de sequência correcta:
- Lead: "Quero ajuda com o marketing"
- Tu: "Percebi! Para eu saber com quem estou a falar: qual é o teu nome e o nome do negócio? Onde fica?"
- Lead: "Sou a [NOME], o negócio é [NEGÓCIO] em [LOCAL]"
- Tu: "Obrigada, [NOME]! Já sei — [NEGÓCIO] em [LOCAL]. E em que área queres mais presença: atrair mais clientes, redes sociais, ou anúncios?"
- (só depois disto é que entras em Fase 2/roteamento)

### Fase 2 — Roteamento pela necessidade (só depois de identificar)
Só depois de saberes nome + negócio/sector é que encaminhas entre as linhas:
- {{ROTEAMENTO_LINHA_1}}
- {{ROTEAMENTO_LINHA_2}}
- Ambíguo → uma pergunta clarificadora.
- Fora do âmbito → ver secção 7.

### Fase 3 — Diagnóstico (2 a 4 mensagens)
Depois de identificares o lead e o negócio. Não avanças para preços sem isto. Uma pergunta de cada vez:

1. Situação actual — "O que tens hoje a funcionar? Já tens algo montado, ou está tudo por fazer?"
2. Objectivo — "E o que querias que acontecesse nos próximos meses?"
3. Contexto do negócio — sector, dimensão, se vende local ou online.
4. Decisão — "És tu que decides estas coisas ou há mais alguém envolvido?"

Enquanto ouves, espelhas o problema em linguagem de diagnóstico, não de venda.
Nunca revelas a estratégia completa nesta fase.

### REGRA DOS GANCHOS — NENHUMA RESPOSTA MORTA

**Nunca terminas um turno só com uma afirmação.** Toda a resposta tua (excepto a saudação inicial) tem de acabar com UM gancho que force o lead a continuar — uma pergunta, uma escolha, ou um próximo passo. Uma resposta que só comenta o que o lead disse ("Boa meta", "Percebo", "Faz sentido") e não pergunta nada é uma conversa morta.

Exemplo do que NÃO fazer:
- ❌ "Boa — esse sector tem muito potencial visual no Instagram." (acaba aqui, sem gancho)

Exemplo do que fazer:
- ✅ "Boa — esse sector tem muito potencial visual no Instagram. Qual é o nome da tua marca e onde fica a loja?" (gancho: pede dados)

Cada resposta deve encadear com a anterior e abrir a seguinte. Pensa em cada turno como um elo: *eco do que o lead disse* + *dado novo que pedes* ou *passo seguinte que propões*. Nunca deixes o lead sem saber o que responder a seguir.

### Fase 4 — Orçamento e alinhamento de expectativas
Só depois do diagnóstico. Introduzes o valor de forma natural:

"Para alinharmos expectativas: trabalhamos com {{SERVICO_ANCORA}} a partir de {{PRECO_ANCORA}}/mês para o serviço base. Faz sentido nessa ordem de grandeza, ou estavas a pensar noutro patamar?"

Grelha interna (não envias em bloco — usas para responder a perguntas concretas):

{{GRELHA_PRECOS}}

Regras de preço:
- Nunca inventas um valor fora da grelha. Se não souberes: "Esse âmbito sai da tabela padrão — o {{NOME_DO_RESPONSAVEL}} prepara-te um valor certo. Consigo ter isso contigo em 24h."
- Investimento em anúncios (se aplicável) é sempre separado da gestão.
- Se pedirem preço logo na primeira mensagem: dá a âncora e pede contexto antes de aprofundar.
- Se orçamento abaixo do mínimo: não desprezas, ofereces alternativa mais leve. Marcas qualificado: false, status: nutricao.
- Nunca dás desconto por iniciativa própria. Se pedirem: "Isso é conversa para ter com o {{NOME_DO_RESPONSAVEL}} — o que posso fazer é ajustar o âmbito para caber no teu orçamento."

### Fase 5 — Objecções

"É caro" → "Percebo. Vale a pena comparares com o custo de ter alguém interno ou com o tempo que isso te tira. Se o valor não encaixa agora, ajustamos o âmbito — dizes-me quanto te faz sentido investir por mês?"

"Vou pensar" → "Claro. Só para não ficar no ar: posso marcar 15 minutos com o {{NOME_DO_RESPONSAVEL}} para te apresentar uma proposta concreta e decides depois com números à frente?"

"Já tenho quem faça" → "Ainda bem. E está a correr como querias? Muitas vezes o que falta não é publicar, é estratégia e leitura de resultados. Se quiseres, fazemos um diagnóstico gratuito e ficas com ele mesmo que não avances connosco."

"Faço eu próprio" → "Faz todo o sentido se tiveres tempo. O ponto costuma ser a regularidade — é aí que a maioria trava. Queres que te mostre o que estás a perder hoje?"

"Garantem resultados?" → "Não garantimos números — quem garante, mente. O que garantimos é método, regularidade e relatórios para veres o que está a acontecer."

"Envia proposta por email" → Aceitas, pedes email e nome, marcas proxima_accao: enviar_proposta.

{{OBJECOES_EXTRA}}

### Fase 6 — Fecho: marcar reunião
O teu objectivo de conversão não é fechar a venda — é marcar a reunião. Fecho é do {{NOME_DO_RESPONSAVEL}}.

Quando o lead tiver necessidade clara + orçamento compatível + interesse → propões reunião com TRÊS opções concretas da agenda.

**IMPORTANTE — horários reais da agenda:** no contexto recebes a secção "[AGENDA DO RESPONSÁVEL — HORÁRIOS LIVRES]" com os horários realmente livres (consultados no Google Calendar). Para propor a reunião, acede SEMPRE a essa agenda e escolhe SEMPRE TRÊS opções dessa lista. NUNCA inventes horas nem dias que não estejam lá, e nunca digas "de manhã", "à tarde" ou "a partir das Xh" — dá sempre horas exactas com dia.

"Faz sentido marcarmos 20 minutos com o {{NOME_DO_RESPONSAVEL}} para te apresentar a proposta. Tenho [opção 1 da lista], [opção 2 da lista] ou [opção 3 da lista] — qual te dá mais jeito?"

Ao confirmar, recolhes: nome completo, empresa, telemóvel, email, dia e hora escolhidos, formato (chamada, videochamada ou presencial). Repetes tudo numa mensagem de confirmação.

No METADATA preenches o bloco agendamento com confirmado: true e data_hora_iso no formato ISO 8601 completo da hora escolhida (ex.: "2026-09-10T10:00:00+01:00") — nunca texto solto. O sistema cria o evento no calendário automaticamente e envia-te (alerta) — não precisas de fazer mais nada no código.

### Fase 7 — Encerramento
Fechas sempre com estado definido:
1. Reunião marcada → confirmação + próximo passo.
2. Proposta a caminho → "O {{NOME_DO_RESPONSAVEL}} envia-te a proposta até [prazo]. Fico atenta aqui se tiveres dúvidas entretanto."
3. Não é agora → "Sem problema. Deixo o teu contacto registado e voltamos a falar daqui a [prazo]. Se mudares de ideias, é só mandares mensagem." → status: nutricao
4. Fora de âmbito → encaminhas ou dizes claramente que não é o que fazemos.

---

## 5. HANDOFF PARA O {{NOME_DO_RESPONSAVEL}}

Marcas handoff_humano: true quando:
- o lead pede explicitamente falar com uma pessoa;
- o lead fica qualificado (score ≥ 70);
- surge questão técnica, jurídica, contratual ou de facturação;
- o lead está irritado, confuso ou a perder paciência;
- há reclamação de cliente actual;
- pedem desconto, condições especiais ou pagamento;
- é questão sensível que exija decisão humana.

**Quando o lead pede EXPLICITAMENTE falar com uma pessoa (ou aceita a tua proposta de atendimento humano), além de handoff_humano: true, marcas também `quer_humano: true`.** Isto despoleta o processo automático abaixo — tu não precisas de fazer nada técnico, só marcar o campo. A tua mensagem ao lead nesse momento deve ser curta e nesta linha:

"Claro, [nome]. Vou já contactar o {{NOME_DO_RESPONSAVEL}} — o fundador — para ver se consegue falar contigo agora. Dá-me só um instante, por favor. 🙏"

(o sistema trata do resto: avisa o {{NOME_DO_RESPONSAVEL}}, pergunta-lhe se está disponível, e volta a falar com o lead conforme a resposta — ou propõe marcar hora, se ele não estiver livre.)

**Check-in de satisfação (importante):** a meio da conversa, sobretudo quando já respondeste a várias dúvidas sobre serviços/preços/processo, faz um check-in natural ao lead — sem ser mecânico — para saber se estás a conseguir ajudar ou se ele prefere falar com uma pessoa. Por exemplo:

"A propósito — estou a conseguir esclarecer tudo o que precisas, ou preferes que te passe já para o {{NOME_DO_RESPONSAVEL}}, o fundador, para falar com uma pessoa? 😊"

Faz isto no máximo uma a duas vezes por conversa, e sobretudo antes de propores o fecho ou quando o lead mostra hesitação. Se o lead disser que prefere uma pessoa → quer_humano: true.

Frase de handoff (para casos sem pedido explícito de humano): "Vou passar isto ao {{NOME_DO_RESPONSAVEL}}, que é quem trata destes casos. Confirmas-me só o teu nome e a melhor altura para te contactar?"

Depois do handoff: não continuas a vender. Respondes apenas ao que for perguntado, em modo informativo.

---

## 6. FORMATO DE SAÍDA (crítico — não falhar)

Cada resposta tem DUAS partes:

PARTE 1 — mensagem para o lead, em {{IDIOMA}}, 2 a 6 linhas, sem markdown.

PARTE 2 — bloco estruturado (nunca chega ao lead):

<METADATA>
{
  "linha_negocio": "{{VALORES_LINHA_NEGOCIO}}",
  "nome": "string ou null",
  "empresa": "string ou null",
  "email": "string ou null",
  "setor": "string ou null",
  "necessidade": "string ou null",
  "servico_interesse": [],
  "orcamento_mensal_estimado": null,
  "urgencia": "imediata" | "este_mes" | "proximo_trimestre" | "explorar" | null,
  "decisor": true | false | null,
  "objecao_principal": "preco" | "tempo" | "confianca" | "ja_tem_fornecedor" | "sem_necessidade" | null,
  "score": 0,
  "qualificado": false,
  "handoff_humano": false,
  "quer_humano": false,
  "razao_handoff": null,
  "agendamento": {
    "proposto": false,
    "confirmado": false,
    "data_hora_iso": "OBRIGATÓRIO em formato ISO 8601 completo quando confirmado, ex: '2026-09-11T10:00:00+01:00' — NUNCA texto como 'próxima quinta'",
    "formato": null
  },
  "proxima_accao_sugerida": "continuar_qualificar" | "enviar_proposta" | "agendar_reuniao" | "nutricao" | "encerrar",
  "resumo_para_humano": "string"
}
</METADATA>

Cálculo de score:
- Identificou-se com nome e empresa: +20
- Necessidade concreta identificada: +25
- Orçamento confirmado ≥ {{SCORE_ORCAMENTO_MINIMO}}: +30
- Urgência imediata ou este mês: +15
- É decisor: +10

score ≥ 70 → qualificado: true → alerta imediato ao {{NOME_DO_RESPONSAVEL}}.

---

## 7. LIMITES DO CATÁLOGO

Fora do catálogo ({{FORA_DO_CATALOGO}}):
"Isso não é o nosso foco directo, mas o {{NOME_DO_RESPONSAVEL}} trabalha com parceiros para essa parte. Queres que ele te ponha em contacto?"

{{CASOS_FORA_CATALOGO}}

---

## 8. MULTIMÉDIA E LIMITAÇÕES

- Áudio, imagem ou ficheiro: "Recebi o teu envio. Consigo ajudar melhor por texto — resumes-me em palavras? Ou preferes que o {{NOME_DO_RESPONSAVEL}} te ligue?"
- Link de rede social do lead: aceitas e registas, mas não analisas ao vivo. Dizes que o {{NOME_DO_RESPONSAVEL}} faz o diagnóstico e traz na reunião.
- Pedido de portfólio: envias os links — {{LINKS_PORTFOLIO}}.

---

## 9. FOLLOW-UP

Não fazes follow-up por iniciativa própria. O sistema dispara quando necessário.

+48h sem resposta após diagnóstico: "Olá [nome], ficou pendente a nossa conversa sobre [tema]. Queres que avance com a proposta?"
+7 dias: envia valor, não pressão — uma observação útil sobre o sector.
+21 dias: "Fico por aqui para não estar a insistir. Se um dia fizer sentido, sabes onde estamos." → status: nutricao

Máximo três tentativas.

---

## 10. REGRAS FINAIS INEGOCIÁVEIS

1. Nunca prometes resultados, prazos de retorno ou números.
2. Nunca inventas preços, serviços, clientes ou casos de sucesso.
3. Nunca nomeias clientes actuais sem autorização.
4. Nunca dizes que és humana.
5. Nunca escreves em {{IDIOMA_PROIBIDO}}.
6. Nunca fechas contrato, aceitas pagamento ou negoceias condições.
7. Se não sabes, dizes que não sabes e passas ao {{NOME_DO_RESPONSAVEL}}.
8. Uma conversa boa termina com um próximo passo definido.
9. Nunca terminas um turno (excepto a saudação) sem um gancho que faça o lead responder. Resposta sem pergunta = conversa morta = falha.
10. Recolhes os dados do lead ao longo de toda a conversa (nome, empresa, sector, email, etc.) e preenches o METADATA. Nunca deixas um dado revelado por preencher.
11. O NOME do lead e o NEGÓCIO dele têm de ser pedidos logo no início (Fase 1), antes de apresentares serviços ou fazeres diagnóstico. Uma conversa que chega ao diagnóstico sem saber o nome do lead é uma falha grave.
"""

async def enviar_mensagem(numero: str, texto: str):
    """Envia mensagem via Evolution API"""
    url = f"{EVOLUTION_URL}/message/sendText/{EVOLUTION_INSTANCE}"
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}
    payload = {"number": numero, "text": texto}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=30)
        data = resp.json()
    # Regista o ID da mensagem que NÓS enviamos, para não a confundir com o responsável
    # a escrever no WhatsApp (que é o sinal de que ele assumiu a conversa).
    try:
        mid = str(data.get("key", {}).get("id") or data.get("id") or "")
        if mid:
            mensagens_enviadas.add(mid)
            if len(mensagens_enviadas) > 800:
                mensagens_enviadas.clear()
    except Exception:
        pass
    return data

def limpar_markdown(texto: str) -> str:
    """Remove markdown cru que o WhatsApp mostra mal."""
    import re
    t = texto
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)  # **negrito**
    t = re.sub(r'\*(.+?)\*', r'\1', t)      # *itálico*
    t = re.sub(r'^[#]+\s*', '', t, flags=re.M)  # títulos
    t = re.sub(r'^[-•]\s*', '', t, flags=re.M)  # bullets
    t = re.sub(r'```', '', t)
    t = re.sub(r'`(.+?)`', r'\1', t)
    t = t.replace('_', '')  # itálico underscore (cautela)
    # limpa linhas em branco em excesso
    t = re.sub(r'\n{3,}', '\n\n', t).strip()
    return t

def dividir_em_bolhas(texto: str, max_len: int = 450) -> list[str]:
    """Divide o texto em blocos separados por linha em branco, cada um = uma bolha WhatsApp.
    Se um bloco for muito longo, corta por frases sem partir palavras."""
    import re
    texto_limpo = limpar_markdown(texto)
    blocos = [b.strip() for b in re.split(r'\n\s*\n', texto_limpo) if b.strip()]

    bolhas = []
    for bloco in blocos:
        if len(bloco) <= max_len:
            bolhas.append(bloco)
            continue
        # bloco longo — corta em frases completas
        frases = re.split(r'(?<=[.!?])\s+', bloco)
        atual = ""
        for frase in frases:
            if atual and len(atual) + len(frase) + 1 > max_len:
                bolhas.append(atual.strip())
                atual = frase
            else:
                atual = (atual + " " + frase).strip() if atual else frase
        if atual:
            bolhas.append(atual.strip())
    return bolhas

async def enviar_blocos(numero: str, texto: str):
    """Envia a resposta dividida em várias bolhas, com pausa de 3s entre elas (natural)."""
    bolhas = dividir_em_bolhas(texto)
    if not bolhas:
        return
    for i, bolha in enumerate(bolhas):
        await enviar_mensagem(numero, bolha)
        # Pausa de 3 segundos entre blocos, para parecer humano e natural.
        # (sem pausa depois da última bolha)
        if i < len(bolhas) - 1:
            await asyncio.sleep(3)

def extrair_metadata(resposta_completa: str) -> tuple[str, dict | None]:
    """Separa a mensagem para o lead do bloco METADATA"""
    import re
    metadata = None
    mensagem = resposta_completa

    match = re.search(r'<METADATA>(.*?)</METADATA>', resposta_completa, re.DOTALL)
    if match:
        try:
            metadata = json.loads(match.group(1).strip())
        except Exception:
            pass
        mensagem = resposta_completa[:match.start()].strip()

    return mensagem, metadata

async def obter_resposta_agente(numero: str, mensagem: str) -> tuple[str, dict | None]:
    """Obtém resposta da Assistente com histórico da conversa"""
    from datetime import datetime
    import zoneinfo

    if numero not in historico:
        historico[numero] = []

    historico[numero].append({"role": "user", "content": mensagem})

    if len(historico[numero]) > 20:
        historico[numero] = historico[numero][-20:]

    # Contexto temporal atual — a Assistente precisa de saber que dia/hora é agora
    agora = datetime.now(zoneinfo.ZoneInfo("Europe/Lisbon"))
    dias_semana = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
    data_pt = agora.strftime("%d/%m/%Y")
    hora_pt = agora.strftime("%H:%M")
    dia_nome = dias_semana[agora.weekday()]

    contexto_tempo = (
        f"\n\n[CONTEXTO TEMPORAL ATUAL — CRÍTICO]\n"
        f"Agora é {dia_nome}, dia {data_pt}, às {hora_pt} (hora de Lisboa).\n"
        f"Usa SEMPRE esta informação para calcular datas e horários. "
        f"Quando sugerires reuniões ou te referires a 'hoje', 'amanhã', 'quinta', etc., "
        f"baseia-te SEMPRE nesta data/hora real — nunca inventes o dia da semana. "
        f"A janela de agendamento é de segundas a sextas-feiras, das 10h às 12h e das 14h às 17h."
    )

    # Horários reais livres na agenda do responsável (Google Calendar). Quando o lead
    # quiser marcar reunião, a Assistente SÓ sugere horas desta lista — nunca inventa.
    try:
        horarios_livres = obter_horarios_livres()
        if horarios_livres:
            # Formata de forma legível: "quinta 10/09 às 10:00", "sexta 11/09 às 14:30"...
            from datetime import datetime
            import zoneinfo
            tz_lisboa = zoneinfo.ZoneInfo("Europe/Lisbon")
            horarios_txt = []
            for iso in horarios_livres[:10]:
                try:
                    dt = datetime.fromisoformat(iso).astimezone(tz_lisboa)
                    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
                    horarios_txt.append(f"{dias[dt.weekday()]} {dt.strftime('%d/%m')} às {dt.strftime('%H:%M')}")
                except Exception:
                    horarios_txt.append(iso)
            contexto_tempo += (
                f"\n\n[AGENDA DO RESPONSÁVEL — HORÁRIOS LIVRES]\n"
                f"Estes são os horários REALMENTE livres na agenda do responsável para reuniões "
                f"(já consultados no Google Calendar):\n"
                + "\n".join(f"- {h}" for h in horarios_txt) + "\n"
                f"Quando fores sugerir ou marcar uma reunião, acede SEMPRE a esta agenda e "
                f"propõe SEMPRE TRÊS opções desta lista (nunca duas, nunca uma). "
                f"NUNCA inventes horas, dias ou períodos que não estejam aqui — nem digas "
                f"coisas como 'a partir das 10h' ou 'de manhã'. Se por algum motivo não "
                f"tiveres horários, diz que vais confirmar a disponibilidade do responsável e "
                f"que ele te responde com a hora exacta."
            )
        else:
            contexto_tempo += (
                f"\n\n[AGENDA DO RESPONSÁVEL]\nNão foi possível obter os horários livres agora "
                f"(ou a agenda está cheia). Se o lead quiser marcar, propõe falar com o "
                f"responsável diretamente para combinar a melhor altura."
            )
    except Exception as e:
        print(f"⚠️ Falha ao obter disponibilidade: {e}")

    mensagem_lead = ""
    metadata = None

    async with httpx.AsyncClient() as client:
        for tentativa in range(3):
            try:
                resposta = await client.post(
                    f"{DEEPSEEK_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                    json={
                        "model": DEEPSEEK_MODEL,
                        # 2048 evita cortar o bloco METADATA (alertas/agendamento perder-se-iam)
                        "max_tokens": 2048,
                        "messages": [{"role": "system", "content": SYSTEM_PROMPT + contexto_tempo}] + historico[numero],
                    },
                    timeout=60,
                )
                resposta.raise_for_status()
                resposta_completa = resposta.json()["choices"][0]["message"]["content"]
            except Exception as e:
                print(f"⚠️ Tentativa {tentativa+1}/3 falhou na API: {e}")
                if tentativa == 2:
                    raise
                await asyncio.sleep(1)
                continue

            # Valida que a resposta tem texto bruto útil
            texto_bruto = (resposta_completa or "").strip()
            if not texto_bruto:
                print(f"⚠️ Tentativa {tentativa+1}/3 devolveu resposta vazia, a repetir...")
                if tentativa == 2:
                    mensagem_lead = "Peço desculpa, tive uma falha técnica momentânea. Podes repetir o que precisavas? 🙏"
                else:
                    await asyncio.sleep(1)
                    continue

            # Separa a mensagem para o lead do bloco METADATA e valida que há texto real
            msg_candidata, meta_candidata = extrair_metadata(resposta_completa)
            if msg_candidata.strip():
                mensagem_lead = msg_candidata
                metadata = meta_candidata
                break
            # Resposta só com METADATA, sem texto para o lead — repetir
            print(f"⚠️ Tentativa {tentativa+1}/3 respondeu sem texto para o lead, a repetir...")
            if tentativa == 2:
                mensagem_lead = "Peço desculpa, tive uma falha técnica momentânea. Podes repetir o que precisavas? 🙏"
            else:
                await asyncio.sleep(1)

    # Guarda no histórico APENAS a mensagem limpa (sem o JSON METADATA),
    # para não poluir o contexto do modelo nos turnos seguintes
    historico[numero].append({"role": "assistant", "content": mensagem_lead})

    return mensagem_lead, metadata

async def guardar_lead_airtable(numero: str, metadata: dict):
    """Faz upsert do lead no Airtable pelo número de telefone"""
    try:
        from datetime import datetime, timezone
        agora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        agendamento = metadata.get("agendamento", {})
        decisor = metadata.get("decisor")

        campos = {
            "Name": metadata.get("nome") or numero,
            "Telefone": numero.replace("@s.whatsapp.net", ""),
            "Linha_Negocio": metadata.get("linha_negocio") or "Indeciso",
            "Setor": metadata.get("setor") or "",
            "Necessidade": metadata.get("necessidade") or "",
            "Resumo_Humano": metadata.get("resumo_para_humano") or "",
            "Ultima_Interacao": agora,
            "Alerta_Enviado": metadata.get("handoff_humano", False) or metadata.get("qualificado", False),
        }

        if metadata.get("empresa"):
            campos["Empresa"] = metadata["empresa"]
        if metadata.get("email"):
            campos["Email"] = metadata["email"]
        if metadata.get("orcamento_mensal_estimado"):
            campos["Orcamento_Mensal"] = metadata["orcamento_mensal_estimado"]
        if metadata.get("urgencia"):
            campos["Urgencia"] = metadata["urgencia"]
        if decisor is not None:
            campos["Decisor"] = decisor
        if metadata.get("objecao_principal"):
            campos["Objeccao"] = metadata["objecao_principal"]
        if metadata.get("score") is not None:
            campos["Score"] = metadata["score"]
        if metadata.get("proxima_accao_sugerida"):
            campos["Lead_Status"] = {
                "continuar_qualificar": "em_qualificacao",
                "agendar_reuniao": "reuniao_marcada",
                "enviar_proposta": "proposta_enviada",
                "nutricao": "nutricao",
                "encerrar": "encerrado",
            }.get(metadata["proxima_accao_sugerida"], "novo")
        # Correcções de estado: um pedido de humano não é uma reunião marcada.
        if metadata.get("quer_humano"):
            campos["Lead_Status"] = "handoff_humano"
        elif metadata.get("handoff_humano") and not agendamento.get("confirmado"):
            # Handoff pedido (sem reunião confirmada) — estado próprio
            if campos.get("Lead_Status") in (None, "novo", "reuniao_marcada"):
                campos["Lead_Status"] = "handoff_humano"
        # Só marca reunião marcada quando há de facto agendamento confirmado
        if agendamento.get("confirmado") and agendamento.get("data_hora_iso"):
            campos["Lead_Status"] = "reuniao_marcada"
        if agendamento.get("data_hora_iso"):
            campos["Agendamento_ISO"] = agendamento["data_hora_iso"]

        payload = {
            "performUpsert": {"fieldsToMergeOn": ["Telefone"]},
            "typecast": True,
            "records": [{"fields": campos}]
        }

        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}"
        headers = {
            "Authorization": f"Bearer {AIRTABLE_TOKEN}",
            "Content-Type": "application/json"
        }

        async with httpx.AsyncClient() as client:
            resp = await client.patch(url, headers=headers, json=payload, timeout=15)
            data = resp.json()

        if "records" in data:
            print(f"✅ Airtable: lead guardado ({campos.get('Name', numero)})")
        else:
            print(f"❌ Airtable erro: {data}")

    except Exception as e:
        print(f"❌ Erro Airtable: {e}")


async def enviar_alerta_telegram(numero: str, metadata: dict):
    """Envia alerta ao responsável via Telegram quando lead qualifica ou pede handoff"""
    try:
        score = metadata.get("score", 0)
        nome = metadata.get("nome") or "Desconhecido"
        empresa = metadata.get("empresa") or "—"
        setor = metadata.get("setor") or "—"
        necessidade = metadata.get("necessidade") or "—"
        orcamento = metadata.get("orcamento_mensal_estimado")
        orcamento_txt = f"{orcamento}€/mês" if orcamento else "—"
        handoff = metadata.get("handoff_humano", False)
        razao_handoff = metadata.get("razao_handoff") or "—"
        proxima = metadata.get("proxima_accao_sugerida", "—")
        resumo = metadata.get("resumo_para_humano", "—")
        agendamento = metadata.get("agendamento", {})
        agend_confirmado = agendamento.get("confirmado", False)
        agend_data = agendamento.get("data_hora_iso") or "—"
        agend_formato = agendamento.get("formato") or "—"

        tipo = "🚨 HANDOFF" if handoff else "⭐ LEAD QUALIFICADO"

        texto = (
            f"{tipo} — Assistente WhatsApp\n\n"
            f"📱 Número: {numero}\n"
            f"👤 Nome: {nome}\n"
            f"🏢 Empresa: {empresa}\n"
            f"🏷️ Sector: {setor}\n"
            f"💬 Necessidade: {necessidade}\n"
            f"💶 Orçamento: {orcamento_txt}\n"
            f"📊 Score: {score}/100\n"
        )

        if handoff:
            texto += f"⚠️ Razão handoff: {razao_handoff}\n"

        if agend_confirmado:
            texto += f"\n📅 REUNIÃO CONFIRMADA\n🗓️ Data: {agend_data}\n📞 Formato: {agend_formato}\n"

        texto += f"\n➡️ Próxima acção: {proxima}\n\n📝 {resumo}"

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            await client.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": texto,
                "parse_mode": "HTML"
            }, timeout=10)

        print(f"✅ Alerta Telegram enviado para o responsável")

    except Exception as e:
        print(f"❌ Erro ao enviar Telegram: {e}")


async def enviar_telegram_responsavel(texto: str):
    """Envia uma mensagem de texto simples ao responsável no Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": texto}, timeout=10)
    except Exception as e:
        print(f"❌ Erro Telegram (responsável): {e}")


def _responsavel_disse_sim(texto: str) -> bool:
    """Interpreta a resposta do responsável: disponível (sim) ou não."""
    import re
    t = (texto or "").strip().lower()
    if not t:
        return False
    # Negações em qualquer posição contam como NÃO (ex.: "claro que não", "agora não posso")
    if re.search(r"\b(nao|não|no|nunca|ocupado|depois|indisponivel|indisponível)\b", t):
        return False
    if t in ("n", "0", "2"):
        return False
    positivos = ("sim", "s", "yes", "y", "ok", "claro", "disponivel", "disponível",
                 "estou", "pode", "1", "vou", "aceito", "ja", "já", "bora", "manda")
    return any(t.startswith(pos) for pos in positivos)


async def loop_polling_telegram():
    """Long-polling das mensagens que o responsável envia ao bot no Telegram.
    Serve para (1) receber a resposta SIM/NÃO ao pedido de handoff e
    (2) comandos de controlo (ex.: /retomar)."""
    global telegram_offset
    print("🔄 Telegram polling iniciado")
    # Descarta mensagens antigas acumuladas antes do arranque
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": -1, "timeout": 0}, timeout=10,
            )
            ups = r.json().get("result", [])
            if ups:
                telegram_offset = ups[-1]["update_id"] + 1
            # Remove qualquer webhook para permitir getUpdates
            await client.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook", timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram polling (init): {e}")

    while True:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params={"offset": telegram_offset, "timeout": 25}, timeout=35,
                )
                data = r.json()
            for upd in data.get("result", []):
                telegram_offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat_id = str(msg.get("chat", {}).get("id", ""))
                texto = (msg.get("text") or "").strip()
                if str(chat_id) != str(TELEGRAM_CHAT_ID) or not texto:
                    continue
                print(f"📨 Telegram do responsável: {texto}")
                await tratar_mensagem_responsavel(texto)
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"⚠️ Telegram polling: {e}")
            await asyncio.sleep(3)


async def tratar_mensagem_responsavel(texto: str):
    """Processa uma mensagem vinda do responsável: comandos ou resposta a handoff."""
    t = (texto or "").strip()
    low = t.lower()

    # Comando: /retomar -> a Assistente volta a responder às conversas em pausa
    if low.startswith("/retomar") or low.startswith("retomar"):
        partes = t.split()
        if len(partes) >= 2 and partes[1].strip():
            alvo = partes[1].strip().replace("+", "").replace(" ", "")
            removidos = [n for n in list(conversa_pausada) if alvo in n]
            for n in removidos:
                conversa_pausada.pop(n, None)
            await enviar_telegram_responsavel(
                f"✅ Assistente retomou {len(removidos)} conversa(s) com {alvo}." if removidos
                else f"⚠️ Não encontrei conversa em pausa com {alvo}."
            )
        else:
            n = len(conversa_pausada)
            conversa_pausada.clear()
            await enviar_telegram_responsavel(f"✅ Assistente retomou {n} conversa(s) em pausa.")
        return

    # Resposta a um pedido de handoff pendente
    if handoff_pendente:
        # Aplica ao pedido mais recente
        numero = list(handoff_pendente.keys())[-1]
        pend = handoff_pendente[numero]
        if not pend["evento"].is_set():
            pend["resposta"] = "sim" if _responsavel_disse_sim(t) else "nao"
            pend["evento"].set()
            print(f"✅ Resposta do responsável ao handoff de {numero}: {pend['resposta']}")
        return

    # Sem handoff pendente: mensagem livre — regista no histórico se for de uma conversa conhecida
    print("ℹ️ Telegram do responsável sem handoff pendente (ignorado)")


async def _opcoes_agenda(n: int = 3) -> list[str]:
    """Devolve até n horários livres legíveis (a partir da agenda real)."""
    from datetime import datetime
    import zoneinfo
    try:
        slots = obter_horarios_livres()
    except Exception:
        slots = []
    tz = zoneinfo.ZoneInfo("Europe/Lisbon")
    dias = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
    out = []
    for iso in slots[:n]:
        try:
            dt = datetime.fromisoformat(iso).astimezone(tz)
            out.append(f"{dias[dt.weekday()]} {dt.strftime('%d/%m')} às {dt.strftime('%H:%M')}")
        except Exception:
            continue
    return out


async def processar_handoff(numero: str, metadata: dict):
    """Pedido de atendimento humano: pergunta ao responsável (Telegram) se está disponível
    e decide o que dizer ao cliente — ou propõe marcar hora, se ele não estiver livre."""
    nome = metadata.get("nome") or "O cliente"

    # 1. Perguntar ao responsável se está disponível agora
    pend = {"evento": asyncio.Event(), "resposta": None}
    handoff_pendente[numero] = pend
    await enviar_telegram_responsavel(
        f"🙋 PEDIDO DE ATENDIMENTO HUMANO\n\n"
        f"👤 {nome}\n"
        f"📱 +{numero.split('@')[0]}\n\n"
        f"O cliente quer falar com uma pessoa.\n"
        f"Estás disponível para assumir a conversa agora?\n\n"
        f"Responde SIM para assumir, ou NÃO para eu marcar um horário.\n"
        f"(Ou escreve directamente no WhatsApp do cliente — a Assistente cala-se assim que escreveres.)\n"
        f"Tens {HANDOFF_TIMEOUT} segundos para responder."
    )

    # 2. Esperar resposta até HANDOFF_TIMEOUT segundos
    try:
        await asyncio.wait_for(pend["evento"].wait(), timeout=HANDOFF_TIMEOUT)
    except asyncio.TimeoutError:
        pass
    handoff_pendente.pop(numero, None)

    disponivel = pend.get("resposta") == "sim"

    if disponivel:
        # O responsável assumiu: Assistente cala-se nesta conversa mas guarda o contexto
        conversa_pausada[numero] = time.time()
        await enviar_blocos(
            numero,
            f"Boa notícia, {nome}! O responsável está disponível e vai falar contigo aqui mesmo dentro de instantes. 🙌"
        )
        print(f"🖐️ Handoff aceite pelo responsável — Assistente em pausa em {numero}")
        return

    # 3. responsável indisponível (respondeu não ou não respondeu a tempo)
    opcoes = await _opcoes_agenda(3)
    if opcoes:
        lista = "\n".join(f"{i+1}. {o}" for i, o in enumerate(opcoes))
        await enviar_blocos(
            numero,
            f"O responsável está num outro atendimento neste momento, {nome}. "
            f"Assim que estiver livre, será ele a falar contigo.\n\n"
            f"Se preferires não esperar, posso já marcar um horário para falarem com calma. "
            f"Tenho estes disponíveis:\n\n{lista}\n\nQual te dá mais jeito?"
        )
    else:
        await enviar_blocos(
            numero,
            f"O responsável está num outro atendimento neste momento, {nome}. "
            f"Assim que estiver livre, será ele a falar contigo por aqui."
        )
    print(f"⏳ Handoff sem resposta/disponibilidade — opções enviadas a {numero}")


def processar_metadata(numero: str, metadata: dict):
    """Processa o bloco de metadata — logs, alertas futuros"""
    if not metadata:
        return

    score = metadata.get("score", 0)
    qualificado = metadata.get("qualificado", False)
    handoff = metadata.get("handoff_humano", False)
    proxima = metadata.get("proxima_accao_sugerida", "")
    resumo = metadata.get("resumo_para_humano", "")

    print(f"📊 [{numero}] Score: {score} | Qualificado: {qualificado} | Handoff: {handoff} | Próxima: {proxima}")

    # Guarda/actualiza lead no Airtable em cada interacção
    asyncio.create_task(guardar_lead_airtable(numero, metadata))

    # Pedido explícito de atendimento humano → fluxo bidirecional com o responsável
    if metadata.get("quer_humano") and numero not in handoff_pendente:
        # Evita re-disparar logo a seguir a um handoff (cooldown simples por número)
        ultimo = handoff_realizado.get(numero, 0)
        if time.time() - ultimo > 120:
            handoff_realizado[numero] = time.time()
            print(f"🙋 HANDOFF HUMANO [{numero}] — a contactar o responsável")
            asyncio.create_task(processar_handoff(numero, metadata))

    # Alerta ao responsável — apenas quando o estado relevante muda, para não repetir.
    # Quando é um pedido explícito de humano (quer_humano), o fluxo processar_handoff
    # já envia a sua própria mensagem ao responsável — não duplicamos aqui.
    if (qualificado or handoff) and not metadata.get("quer_humano"):
        agendamento_md = metadata.get("agendamento", {})
        assinatura = json.dumps({
            "handoff": handoff,
            "qualificado": qualificado,
            "razao": metadata.get("razao_handoff"),
            "confirmado": agendamento_md.get("confirmado", False),
            "data": agendamento_md.get("data_hora_iso"),
        }, ensure_ascii=False, sort_keys=True)
        if alerta_estado.get(numero) != assinatura:
            alerta_estado[numero] = assinatura
            print(f"🚨 ALERTA RESPONSÁVEL [{numero}]: {resumo}")
            asyncio.create_task(enviar_alerta_telegram(numero, metadata))

    # Cria evento no Calendar se reunião confirmada — dedup por data
    agendamento = metadata.get("agendamento", {})
    if agendamento.get("confirmado") and agendamento.get("data_hora_iso"):
        chave_evento = agendamento["data_hora_iso"]
        if calendario_criado.get(numero) != chave_evento:
            calendario_criado[numero] = chave_evento
            asyncio.create_task(criar_evento_calendar(metadata, numero))

@app.post("/webhook")
async def webhook(request: Request):
    """Recebe eventos da Evolution API"""
    try:
        body = await request.json()
        evento = body.get("event", "")
        
        # Log de todos os eventos para debug
        print(f"🔔 Evento recebido: {evento} | Keys: {list(body.keys())}")

        if evento != "messages.upsert":
            return {"status": "ignored"}

        data = body.get("data", {})
        key = data.get("key", {})
        numero = key.get("remoteJid", "")
        msg = data.get("message", {}) or {}

        # ── MENSAGEM ENVIADA PELO PRÓPRIO NÚMERO (fromMe) ──
        # Se for uma mensagem que NÓS não enviámos, é o responsável a escrever
        # directamente no WhatsApp → ele assumiu a conversa → Assistente fica em pausa.
        if key.get("fromMe", False):
            if numero.endswith("@s.whatsapp.net"):
                mid = str(key.get("id", ""))
                if mid and mid not in mensagens_enviadas:
                    texto_responsavel = (msg.get("conversation", "")
                                   or msg.get("extendedTextMessage", {}).get("text", ""))
                    if texto_responsavel:
                        # Só pausa se ainda não estava em pausa (evita log repetido)
                        ja_pausada = numero in conversa_pausada
                        conversa_pausada[numero] = time.time()
                        # Guarda no histórico para a Assistente manter o contexto da conversa
                        historico.setdefault(numero, []).append(
                            {"role": "assistant", "content": texto_responsavel})
                        if not ja_pausada:
                            print(f"🖐️ responsável assumiu a conversa com {numero} — Assistente em pausa")
            return {"status": "ignored"}

        mensagem = msg.get("conversation", "") or ""
        if not mensagem:
            mensagem = msg.get("extendedTextMessage", {}).get("text", "")

        if not numero:
            return {"status": "ignored"}

        # Guarda contra duplicados — mesmo ID já processado recentemente
        global processados_recentes
        msg_id = str(key.get("id", "")) + numero
        if msg_id in processados_recentes:
            print(f"⏭️ Duplicado ignorado: {msg_id}")
            return {"status": "duplicate"}
        processados_recentes.add(msg_id)
        if len(processados_recentes) > 300:
            processados_recentes.clear()

        # Envios multimédia sem texto (áudio, imagem, documento, vídeo, sticker).
        # O prompt secção 8 promete resposta; antes estes caíam em silêncio.
        if not mensagem:
            tem_media = any(k in msg for k in (
                "audioMessage", "pttMessage", "imageMessage", "documentMessage",
                "videoMessage", "stickerMessage", "viewOnceMessageV2", "contactMessage",
            ))
            # Em conversa 1:1 responde; em grupos ignora para evitar ruído
            if tem_media and numero.endswith("@s.whatsapp.net"):
                await enviar_blocos(numero,
                    "Recebi o teu envio. Consigo ajudar melhor por texto — "
                    "resumes-me em palavras? Ou preferes que o responsável te ligue?")
                print(f"📎 [{numero}] media recebida — resposta padrão")
            return {"status": "ignored"}

        print(f"📩 [{numero}]: {mensagem}")

        # ── AGENTE EM PAUSA: o responsável assumiu esta conversa ──
        # Guarda a mensagem no histórico (contexto preservado) mas NÃO responde.
        ts_pausa = conversa_pausada.get(numero)
        if ts_pausa:
            if time.time() - ts_pausa > PAUSA_TTL:
                # Pausa expirou (sem interacção do responsável há muito tempo) — Assistente retoma
                conversa_pausada.pop(numero, None)
            else:
                historico.setdefault(numero, []).append({"role": "user", "content": mensagem})
                print(f"🖐️ [{numero}] em pausa (responsável a atender) — contexto guardado, sem resposta")
                return {"status": "paused"}

        # ── DEBOUNCE: acumula os blocos do cliente e espera ~5s de silêncio ──
        fila_turno.setdefault(numero, []).append(mensagem)

        # Se já existe um worker deste número a processar, apenas acrescenta à fila.
        # Caso contrário, cria um worker que espera silêncio e depois responde.
        if numero not in workers or workers[numero].done():
            workers[numero] = asyncio.create_task(worker_numero(numero))
            print(f"🧵 [{numero}] worker criado | fila: {len(fila_turno.get(numero, []))} msg")
        else:
            print(f"🧵 [{numero}] worker ja ativo | fila: {len(fila_turno.get(numero, []))} msg")

        return {"status": "ok"}

    except Exception as e:
        import traceback
        print(f"❌ Erro: {traceback.format_exc()}")
        return {"status": "error", "detail": str(e)}

async def worker_numero(numero: str):
    """Worker persistente por número: espera o cliente 'parar' de escrever e responde.
    Acumula todos os blocos que chegarem durante a janela de silêncio, junta-os e
    responde uma única vez com o contexto completo."""
    while True:
        # Espera um pouco; se durante a espera chegar mais texto, o loop continua
        # a acumular (o while volta a verificar a fila).
        try:
            await asyncio.sleep(DEBOUNCE_SEG)
            print(f"⏰ [{numero}] worker acordou apos {DEBOUNCE_SEG}s")
        except asyncio.CancelledError:
            return

        # Protege contra processamento simultâneo do mesmo número
        lock = lock_por_numero.setdefault(numero, asyncio.Lock())
        async with lock:
            if numero not in fila_turno or not fila_turno[numero]:
                # Fila vazia — nenhum turno pendente, worker pode terminar
                break
            blocos_cliente = fila_turno.pop(numero)

            if len(blocos_cliente) == 1:
                texto_cliente = blocos_cliente[0]
            else:
                texto_cliente = " ".join(b.strip() for b in blocos_cliente if b.strip())
            print(f"⏳ [{numero}] turno completo ({len(blocos_cliente)} blocos): {texto_cliente}")

            try:
                mensagem_lead, metadata = await obter_resposta_agente(numero, texto_cliente)
                print(f"🤖 Assistente: {mensagem_lead}")
                processar_metadata(numero, metadata)
                await enviar_blocos(numero, mensagem_lead)
            except Exception as e:
                import traceback
                print(f"❌ Erro ao processar turno: {traceback.format_exc()}")
                try:
                    await enviar_blocos(numero,
                        "Peço desculpa, tive uma falha técnica momentânea. Podes repetir? 🙏")
                except Exception:
                    pass

        # Depois de responder, volta ao início: espera de novo. Se durante a
        # resposta chegaram novas mensagens do cliente, a fila não está vazia
        # e processa-as no próximo ciclo (respeitando o silêncio de novo).

@app.get("/")
def health():
    return {"status": "Assistente — Assistente a empresa online 🤖"}


@app.on_event("startup")
async def _iniciar_polling_telegram():
    """Arranca o long-polling do Telegram (respostas do responsável ao handoff)."""
    asyncio.create_task(loop_polling_telegram())
