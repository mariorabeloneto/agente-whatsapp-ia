"""Testes do modo pausa (kill switch) e do relatório de resultados."""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agente"))

from memory import HistoricoStore  # noqa: E402
import relatorio  # noqa: E402


def run(c):
    return asyncio.run(c)


# ── Modo pausa ──

def test_flag_desligada_por_omissao():
    store = HistoricoStore(redis_url=None)
    assert run(store.get_flag("pausa_global")) is False


def test_ligar_e_desligar_pausa():
    store = HistoricoStore(redis_url=None)
    run(store.set_flag("pausa_global", True))
    assert run(store.get_flag("pausa_global")) is True
    run(store.set_flag("pausa_global", False))
    assert run(store.get_flag("pausa_global")) is False


# ── Relatório ──

def _traces(tmp_path, registos):
    f = tmp_path / "conversations.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in registos), encoding="utf-8")
    return f


def test_relatorio_vazio(tmp_path):
    f = _traces(tmp_path, [])
    d = relatorio.gerar_relatorio(caminho=f)
    assert d["conversas"] == 0
    assert "Sem conversas" in relatorio.formatar_relatorio(d)


def test_relatorio_agrega(tmp_path):
    regs = [
        {"ts": "2026-09-10T10:00:00+00:00", "numero": "a@s", "cost_usd": 0.002,
         "latency_ms": 1000, "qualificado": True, "agendamento_confirmado": True},
        {"ts": "2026-09-10T10:01:00+00:00", "numero": "a@s", "cost_usd": 0.001,
         "latency_ms": 2000, "qualificado": True},
        {"ts": "2026-09-10T10:02:00+00:00", "numero": "b@s", "cost_usd": 0.003,
         "latency_ms": 3000, "handoff": True},
    ]
    f = _traces(tmp_path, regs)
    d = relatorio.gerar_relatorio(caminho=f)
    assert d["conversas"] == 2
    assert d["turnos"] == 3
    assert d["leads_qualificados"] == 1
    assert d["marcacoes"] == 1
    assert d["handoffs"] == 1
    assert d["custo_total"] == 0.006
    assert d["custo_medio_conversa"] == 0.003
    assert d["latencia_media_ms"] == 2000


def test_relatorio_formato(tmp_path):
    f = _traces(tmp_path, [
        {"ts": "2026-09-10T10:00:00+00:00", "numero": "a@s", "cost_usd": 0.002,
         "latency_ms": 1000, "agendamento_confirmado": True},
    ])
    txt = relatorio.formatar_relatorio(relatorio.gerar_relatorio(caminho=f))
    assert "Conversas: 1" in txt
    assert "Reuniões marcadas: 1" in txt
    assert "$" in txt


def test_relatorio_ignora_linhas_mas(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text('{"ts":"2026-09-10T10:00:00+00:00","numero":"a@s"}\nlixo nao json\n', encoding="utf-8")
    d = relatorio.gerar_relatorio(caminho=f)
    assert d["conversas"] == 1
