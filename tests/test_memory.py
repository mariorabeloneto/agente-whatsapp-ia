"""Testes da persistência do histórico (memory.py)."""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agente"))

from memory import HistoricoStore  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_fallback_sem_redis():
    store = HistoricoStore(redis_url=None, max_len=20)
    assert store.persistente is False
    run(store.append("3519@s.whatsapp.net", "user", "olá"))
    run(store.append("3519@s.whatsapp.net", "assistant", "olá!"))
    hist = run(store.get("3519@s.whatsapp.net"))
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert run(store.count()) == 1


def test_trim_ao_max_len():
    store = HistoricoStore(redis_url=None, max_len=3)
    for i in range(10):
        run(store.append("n", "user", f"m{i}"))
    hist = run(store.get("n"))
    assert len(hist) == 3
    assert hist[-1]["content"] == "m9"


def test_clear():
    store = HistoricoStore(redis_url=None, max_len=20)
    run(store.append("n", "user", "x"))
    run(store.clear("n"))
    assert run(store.get("n")) == []


def test_set_trunca():
    store = HistoricoStore(redis_url=None, max_len=2)
    run(store.set("n", [{"role": "user", "content": str(i)} for i in range(5)]))
    assert len(run(store.get("n"))) == 2


def _redis_disponivel() -> bool:
    try:
        import redis.asyncio as aioredis
    except Exception:
        return False

    async def _ping():
        r = aioredis.from_url("redis://localhost:6379/15", decode_responses=True)
        await r.ping()
        await r.aclose()

    try:
        asyncio.run(_ping())
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _redis_disponivel(), reason="sem Redis local")
def test_persistencia_entre_instancias():
    """Simula um restart: um novo store lê o que o anterior gravou."""
    url = "redis://localhost:6379/15"
    s1 = HistoricoStore(redis_url=url, max_len=20)
    assert s1.persistente is True
    run(s1.append("restart@s.whatsapp.net", "user", "antes do restart"))

    s2 = HistoricoStore(redis_url=url, max_len=20)  # nova "instância" (restart)
    hist = run(s2.get("restart@s.whatsapp.net"))
    assert hist and hist[-1]["content"] == "antes do restart"
    run(s2.clear("restart@s.whatsapp.net"))
