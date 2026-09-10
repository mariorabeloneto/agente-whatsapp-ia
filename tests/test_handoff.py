"""Testes do cancelamento de handoff (bug: mensagem de timeout a atropelar
uma conversa já resolvida)."""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agente"))

import agente  # noqa: E402


def _num(n="351900000001@s.whatsapp.net"):
    return n


def test_cancelar_sem_pendente_devolve_false():
    agente.handoff_pendente.clear()
    assert agente.cancelar_handoff(_num()) is False


def test_cancelar_pendente_activo():
    agente.handoff_pendente.clear()
    ev = asyncio.Event()
    agente.handoff_pendente[_num()] = {"evento": ev, "resposta": None, "cancelado": False}
    assert agente.cancelar_handoff(_num()) is True
    p = agente.handoff_pendente[_num()]
    assert p["cancelado"] is True
    assert ev.is_set(), "o evento tem de acordar o processar_handoff"


def test_cancelar_ja_resolvido_devolve_false():
    agente.handoff_pendente.clear()
    ev = asyncio.Event()
    ev.set()
    agente.handoff_pendente[_num()] = {"evento": ev, "resposta": "sim", "cancelado": False}
    assert agente.cancelar_handoff(_num()) is False


def test_handoff_cancelado_nao_envia_mensagem_de_timeout(monkeypatch):
    """Cenário do bug: o handoff expira DEPOIS de a conversa ter continuado.
    Não pode enviar o bloco 'estou ocupado / tenho estes horários'."""
    enviados = []

    async def fake_telegram(texto):
        pass

    async def fake_blocos(numero, texto):
        enviados.append(texto)

    monkeypatch.setattr(agente, "enviar_telegram_responsavel", fake_telegram)
    monkeypatch.setattr(agente, "enviar_blocos", fake_blocos)
    agente.handoff_pendente.clear()

    async def cenario():
        tarefa = asyncio.create_task(
            agente.processar_handoff(_num("351900000777@s.whatsapp.net"), {"nome": "José"})
        )
        await asyncio.sleep(0.05)  # deixa o handoff ficar pendente
        # o cliente continua a conversa -> cancela
        agente.cancelar_handoff("351900000777@s.whatsapp.net")
        await tarefa

    asyncio.run(cenario())
    assert enviados == [], f"não devia enviar nada ao cliente, enviou: {enviados}"


def test_handoff_sem_resposta_envia_opcoes(monkeypatch):
    """Sem cancelamento e sem resposta do responsável -> envia as opções (comportamento normal)."""
    enviados = []
    agente.HANDOFF_TIMEOUT = 0  # expira imediatamente

    async def fake_telegram(texto):
        pass

    async def fake_blocos(numero, texto):
        enviados.append(texto)

    async def fake_opcoes(n=3):
        return ["segunda 10:00", "segunda 10:30", "segunda 11:00"]

    monkeypatch.setattr(agente, "enviar_telegram_responsavel", fake_telegram)
    monkeypatch.setattr(agente, "enviar_blocos", fake_blocos)
    monkeypatch.setattr(agente, "_opcoes_agenda", fake_opcoes)
    agente.handoff_pendente.clear()

    asyncio.run(agente.processar_handoff("351900000888@s.whatsapp.net", {"nome": "Ana"}))
    assert len(enviados) == 1
    assert "outro atendimento" in enviados[0].lower() or "disponíveis" in enviados[0].lower()
