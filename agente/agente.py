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

# O SYSTEM_PROMPT é carregado no topo do ficheiro (config.py + prompts/system_prompt.md).
#  Comportamento = dados. O agente não sabe quem é o cliente: lê configs/config.yaml
#  e prompts/system_prompt.md em runtime.
from config import load_config, render_prompt
from traces import record_turn, Timer
from memory import HistoricoStore
from security import detetar_injection, aviso_prompt, MAX_EVENTOS_POR_CONVERSA

CONFIG = load_config()
SYSTEM_PROMPT = render_prompt(CONFIG)

# Histórico persistente (Redis se disponível; senão memória)
historico = HistoricoStore(
    redis_url=os.getenv("REDIS_URL"),
    max_len=int(CONFIG.get("max_historico_mensagens", 20)),
    ttl=int(CONFIG.get("historico_ttl_horas", 168)) * 3600,
)

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
    antecedencia_min = timedelta(hours=float(CONFIG.get("antecedencia_min_horas", 2)))

    # Janelas de horário e dias vêm da config (configs/config.yaml -> horarios:)
    hor = CONFIG.get("horarios", {}) or {}
    janelas = [tuple(j) for j in (hor.get("janelas") or [(10, 0, 12, 0), (14, 0, 17, 0)])]
    dias_uteis = set(hor.get("dias_semana", [0, 1, 2, 3, 4]))

    for _ in range(dias):
        if dia.weekday() in dias_uteis:  # dias configurados em config.yaml
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

# Mensagens processadas recentemente (deduplicação de eventos)
processados_recentes: set[str] = set()

# Controlo de duplicados: alerta ao responsável e eventos Calendar por número/conversa.
# Evita bombardear o responsável com o mesmo alerta ou criar eventos duplicados
# quando o modelo repete o METADATA em turnos consecutivos.
alerta_estado: dict[str, str] = {}

# Debounce de resposta: pessoas escrevem em blocos no WhatsApp. Quando uma
# mensagem chega, esperamos DEBOUNCE_SEG para ver se vêm mais blocos do mesmo
# cliente; só respondemos quando ele "pára" de escrever.
DEBOUNCE_SEG = int(CONFIG.get("debounce_seg", 5))
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
HANDOFF_TIMEOUT = int(CONFIG.get("handoff_timeout_seg", 30))
# Duração (seg) que a pausa automática se mantém sem nova interacção do responsável (24h)
PAUSA_TTL = int(CONFIG.get("pausa_ttl_horas", 24)) * 3600

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

def _ferramentas_usadas(metadata: dict | None) -> list[str]:
    """Lista as ferramentas/ações que o turno disparou, a partir do METADATA."""
    if not metadata:
        return []
    tools = []
    ag = metadata.get("agendamento", {}) or {}
    if ag.get("confirmado") and ag.get("data_hora_iso"):
        tools.append("calendar.create_event")
    if ag.get("proposto"):
        tools.append("calendar.check_availability")
    if metadata.get("handoff_humano"):
        tools.append("airtable.upsert_lead")
        tools.append("telegram.alert")
    if metadata.get("quer_humano"):
        tools.append("telegram.request_human")
    if not tools and metadata:
        tools.append("airtable.upsert_lead")
    return tools


async def obter_resposta_agente(numero: str, mensagem: str) -> tuple[str, dict | None, dict]:
    """Obtém resposta da Assistente com histórico da conversa"""
    from datetime import datetime
    import zoneinfo

    hist = await historico.get(numero)
    hist.append({"role": "user", "content": mensagem})
    limite = int(CONFIG.get("max_historico_mensagens", 20))
    if len(hist) > limite:
        hist = hist[-limite:]
    await historico.set(numero, hist)

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

    # ── Guardrail de entrada ──
    _suspeitas = detetar_injection(mensagem)
    if _suspeitas:
        print(f"🛡️ Possível injection detetada: {_suspeitas}")
        contexto_tempo += aviso_prompt()

    mensagem_lead = ""
    metadata = None
    _uso_total = {"prompt_tokens": 0, "completion_tokens": 0}

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
                        "messages": [{"role": "system", "content": SYSTEM_PROMPT + contexto_tempo}] + hist,
                    },
                    timeout=60,
                )
                resposta.raise_for_status()
                _body = resposta.json()
                resposta_completa = _body["choices"][0]["message"]["content"]
                uso = _body.get("usage", {}) or {}
                _uso_total["prompt_tokens"] += uso.get("prompt_tokens", 0)
                _uso_total["completion_tokens"] += uso.get("completion_tokens", 0)
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
    await historico.append(numero, "assistant", mensagem_lead)

    return mensagem_lead, metadata, _uso_total

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


def cancelar_handoff(numero: str, motivo: str = "") -> bool:
    """Cancela um pedido de handoff pendente para este número.

    Usado quando a conversa continua (nova mensagem do cliente) ou quando já
    ficou resolvida (agendamento confirmado) — para não enviar depois a
    mensagem de "estou ocupado", que atropelaria um desfecho já fechado.
    Devolve True se havia algo para cancelar.
    """
    p = handoff_pendente.get(numero)
    if p and not p["evento"].is_set():
        p["cancelado"] = True
        p["evento"].set()
        print(f"↩️ handoff cancelado [{numero}] {motivo}".rstrip())
        return True
    return False


async def processar_handoff(numero: str, metadata: dict):
    """Pedido de atendimento humano: pergunta ao responsável (Telegram) se está disponível
    e decide o que dizer ao cliente — ou propõe marcar hora, se ele não estiver livre."""
    nome = metadata.get("nome") or "O cliente"

    # 1. Perguntar ao responsável se está disponível agora
    pend = {"evento": asyncio.Event(), "resposta": None, "cancelado": False}
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

    # Se a conversa continuou (ou já ficou resolvida) entretanto, não enviamos
    # a mensagem de "estou ocupado" — senão atropelava um desfecho já fechado.
    if pend.get("cancelado"):
        print(f"↩️ Handoff de {numero} cancelado (conversa continuou)")
        return

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
    _ag_ok = (metadata.get("agendamento", {}) or {}).get("confirmado")
    if metadata.get("quer_humano") and numero not in handoff_pendente and not _ag_ok:
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
        # Reunião/visita fechada: cancelar qualquer pedido de humano ainda a decorrer
        cancelar_handoff(numero, "agendamento confirmado")
        chave_evento = agendamento["data_hora_iso"]
        if calendario_criado.get(numero) != chave_evento:
            # Limite de segurança: evita criação em massa de eventos por abuso
            if len(calendario_criado) >= MAX_EVENTOS_POR_CONVERSA and numero not in calendario_criado:
                print(f"🛡️ Limite de eventos atingido — evento de {numero} não criado")
                return
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
                        await historico.append(numero, "assistant", texto_responsavel)
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

        # ── Nova mensagem do cliente: cancelar qualquer handoff a decorrer ──
        #  (o pedido de humano em curso deixou de fazer sentido — a conversa continua)
        cancelar_handoff(numero, "conversa continuou")

        # ── AGENTE EM PAUSA: o responsável assumiu esta conversa ──
        # Guarda a mensagem no histórico (contexto preservado) mas NÃO responde.
        ts_pausa = conversa_pausada.get(numero)
        if ts_pausa:
            if time.time() - ts_pausa > PAUSA_TTL:
                # Pausa expirou (sem interacção do responsável há muito tempo) — Assistente retoma
                conversa_pausada.pop(numero, None)
            else:
                await historico.append(numero, "user", mensagem)
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
                with Timer() as _t:
                    mensagem_lead, metadata, uso = await obter_resposta_agente(numero, texto_cliente)
                print(f"🤖 Assistente: {mensagem_lead}")
                processar_metadata(numero, metadata)
                await enviar_blocos(numero, mensagem_lead)
                # Trace do turno (tokens, custo, latência, decisões)
                try:
                    record_turn(
                        numero,
                        user_message=texto_cliente,
                        assistant_message=mensagem_lead,
                        prompt_tokens=uso.get("prompt_tokens", 0),
                        completion_tokens=uso.get("completion_tokens", 0),
                        latency_ms=_t.elapsed_ms,
                        tools=_ferramentas_usadas(metadata),
                        metadata=metadata,
                        model=DEEPSEEK_MODEL,
                    )
                except Exception as _e:
                    print(f"⚠️ trace: {_e}")
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
async def health():
    """Health/doctor: verifica as dependências e diz o que está partido."""
    import time as _time
    checks = {}

    async def _check(name, coro):
        t0 = _time.perf_counter()
        try:
            ok, detalhe = await coro
        except Exception as e:
            ok, detalhe = False, str(e)[:200]
        checks[name] = {"ok": ok, "detail": detalhe,
                        "ms": round((_time.perf_counter() - t0) * 1000, 1)}

    async def c_evolution():
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{EVOLUTION_URL}/", timeout=8)
            return r.status_code == 200, f"HTTP {r.status_code}"

    async def c_llm():
        if not DEEPSEEK_API_KEY:
            return False, "DEEPSEEK_API_KEY ausente"
        async with httpx.AsyncClient() as c:
            r = await c.get(f"{DEEPSEEK_URL}/models",
                            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"}, timeout=10)
            return r.status_code == 200, f"HTTP {r.status_code}"

    async def c_calendar():
        try:
            get_calendar_service().events().list(calendarId="primary", maxResults=1).execute()
            return True, "ok"
        except Exception as e:
            return False, str(e)[:150]

    async def c_airtable():
        if not (AIRTABLE_TOKEN and AIRTABLE_BASE and AIRTABLE_TABLE):
            return False, "credenciais Airtable ausentes"
        async with httpx.AsyncClient() as c:
            r = await c.get(
                f"https://api.airtable.com/v0/{AIRTABLE_BASE}/{AIRTABLE_TABLE}?maxRecords=1",
                headers={"Authorization": f"Bearer {AIRTABLE_TOKEN}"}, timeout=10)
            return r.status_code == 200, f"HTTP {r.status_code}"

    async def c_telegram():
        if not TELEGRAM_TOKEN:
            return False, "TELEGRAM_TOKEN ausente"
        async with httpx.AsyncClient() as c:
            r = await c.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe", timeout=8)
            return r.status_code == 200, f"HTTP {r.status_code}"

    await asyncio.gather(
        _check("evolution", c_evolution()),
        _check("llm", c_llm()),
        _check("calendar", c_calendar()),
        _check("airtable", c_airtable()),
        _check("telegram", c_telegram()),
    )
    tudo_ok = all(v["ok"] for v in checks.values())
    return {"status": "ok" if tudo_ok else "degraded", "checks": checks,
            "conversas_ativas": await historico.count(), "em_pausa": len(conversa_pausada),
            "historico_persistente": historico.persistente}
@app.on_event("startup")
async def _iniciar_polling_telegram():
    """Arranca o long-polling do Telegram (respostas do responsável ao handoff)."""
    asyncio.create_task(loop_polling_telegram())
