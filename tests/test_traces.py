"""Testes do módulo de traces."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agente"))


def test_estimate_cost_zero():
    import traces
    assert traces.estimate_cost(0, 0) == 0.0


def test_estimate_cost_positivo():
    import traces
    c = traces.estimate_cost(1_000_000, 1_000_000)
    assert c == round(traces.PRICE_IN_PER_M + traces.PRICE_OUT_PER_M, 6) or c > 0


def test_record_turn_escreve_jsonl(tmp_path, monkeypatch):
    import traces
    monkeypatch.setattr(traces, "TRACES_DIR", tmp_path)
    monkeypatch.setattr(traces, "TRACES_FILE", tmp_path / "conversations.jsonl")
    reg = traces.record_turn(
        "3519@s.whatsapp.net",
        user_message="olá",
        assistant_message="olá!",
        prompt_tokens=100,
        completion_tokens=50,
        latency_ms=123.4,
        metadata={"score": 40, "handoff_humano": True},
    )
    assert reg["total_tokens"] == 150
    assert reg["score"] == 40
    linhas = (tmp_path / "conversations.jsonl").read_text().strip().splitlines()
    assert len(linhas) == 1
    assert json.loads(linhas[0])["user_message"] == "olá"


def test_timer_mede():
    import traces
    with traces.Timer() as t:
        pass
    assert t.ms >= 0
