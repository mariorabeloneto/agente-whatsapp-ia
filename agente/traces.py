"""Traces por conversa — a matéria-prima da observabilidade.

Cada turno do agente gera uma linha JSONL com: tokens, custo estimado, latência,
ferramentas chamadas e decisões (score, handoff, agendamento). É o que permite
responder a "isto funciona?" com números em vez de opinião.

Ficheiro de saída: traces/conversations.jsonl (uma linha por turno).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACES_DIR = Path(os.environ.get("TRACES_DIR", ROOT / "traces"))
TRACES_FILE = TRACES_DIR / "conversations.jsonl"

# Privacidade por desenho: se TRACES_ANONIMIZAR=true, os traces NÃO guardam o
# número em claro nem o texto das mensagens (só um hash e o comprimento). Útil
# para cumprir minimização de dados / RGPD.
ANONIMIZAR = os.environ.get("TRACES_ANONIMIZAR", "false").lower() in ("1", "true", "yes", "sim")

# Preço por 1M tokens (USD) — ajustar ao modelo usado. Valores por omissão:
# DeepSeek chat (entrada/saída). Configurável por env.
PRICE_IN_PER_M = float(os.environ.get("PRICE_IN_PER_M", "0.27"))
PRICE_OUT_PER_M = float(os.environ.get("PRICE_OUT_PER_M", "1.10"))


def _ident(numero: str) -> str:
    """Identificador do número: em claro ou anonimizado (hash), conforme a config."""
    if not ANONIMIZAR:
        return numero
    return "anon-" + hashlib.sha256((numero or "").encode()).hexdigest()[:12]


def _texto(t: str) -> str:
    """Texto da mensagem: em claro ou omitido (só comprimento), conforme a config."""
    if not ANONIMIZAR:
        return (t or "")[:500]
    return f"[omitido:{len(t or '')}c]"


def estimate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Custo estimado em USD para um turno."""
    return (prompt_tokens / 1_000_000) * PRICE_IN_PER_M + \
           (completion_tokens / 1_000_000) * PRICE_OUT_PER_M


def record_turn(
    numero: str,
    *,
    user_message: str = "",
    assistant_message: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: float = 0.0,
    tools: list[str] | None = None,
    metadata: dict | None = None,
    model: str = "",
) -> dict:
    """Regista um turno no ficheiro de traces. Devolve o registo."""
    metadata = metadata or {}
    agendamento = metadata.get("agendamento", {}) or {}
    registo = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "numero": _ident(numero),
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": round(estimate_cost(prompt_tokens, completion_tokens), 6),
        "latency_ms": round(latency_ms, 1),
        "tools": tools or [],
        "score": metadata.get("score"),
        "qualificado": metadata.get("qualificado"),
        "handoff": metadata.get("handoff_humano"),
        "quer_humano": metadata.get("quer_humano"),
        "agendamento_confirmado": agendamento.get("confirmado"),
        "proxima_accao": metadata.get("proxima_accao_sugerida"),
        "user_message": _texto(user_message),
        "assistant_message": _texto(assistant_message),
    }
    try:
        TRACES_DIR.mkdir(parents=True, exist_ok=True)
        with open(TRACES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(registo, ensure_ascii=False) + "\n")
    except Exception as e:  # nunca deixar uma falha de trace partir o turno
        print(f"⚠️ Falha ao gravar trace: {e}")
    return registo


class Timer:
    """Mede a latência de um bloco: with Timer() as t: ... ; t.ms"""

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = (time.perf_counter() - self._t0) * 1000

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000
