"""Scorer de evals — avalia automaticamente se uma resposta do agente cumpriu
o que o caso de teste esperava, sem precisar de julgamento humano.

Regras (locais, determinísticas):
- `expect_tools`: as ferramentas esperadas têm de estar entre as usadas.
- `expect_metadata`: campos do METADATA com o valor esperado.
- `must_contain` / `must_not_contain`: substrings na mensagem ao lead.
- `expect_phase`: heurística de fase (abertura/identificacao/diagnostico/preco/fecho).
"""
from __future__ import annotations


def _fase(mensagem: str) -> str:
    """Heurística simples da fase da conversa a partir da mensagem."""
    m = mensagem.lower()
    if "assistente virtual" in m and "em que posso ajudar" in m:
        return "abertura"
    if (("nome" in m and ("negócio" in m or "negocio" in m or "teu" in m))
            or "como te chamas" in m or "nome do teu" in m):
        return "identificacao"
    if any(p in m for p in ("o que tens hoje", "o que procuras", "querias que acontecesse")):
        return "diagnostico"
    if "€" in m or "/mês" in m or "investimento" in m:
        return "preco"
    if any(p in m for p in ("marcar", "reunião", "reuniao", "agenda", "20 minutos")):
        return "fecho"
    return "indefinida"


def score_case(case: dict, result: dict) -> dict:
    """Avalia um caso. `result` = {message, metadata, tools}.
    Devolve {passed: bool, checks: {nome: bool}}."""
    checks: dict[str, bool] = {}
    msg = result.get("message", "") or ""
    md = result.get("metadata") or {}
    tools = set(result.get("tools") or [])

    if "expect_tools" in case:
        esperadas = set(case["expect_tools"])
        checks["tools"] = esperadas.issubset(tools)

    for campo, valor in (case.get("expect_metadata") or {}).items():
        obtido = md.get(campo)
        if isinstance(valor, dict) and isinstance(obtido, dict):
            # compara só as sub-chaves indicadas
            ok = all(obtido.get(k) == v for k, v in valor.items())
        else:
            ok = obtido == valor
        checks[f"metadata.{campo}"] = ok

    for sub in (case.get("must_contain") or []):
        checks[f"contem:{sub[:20]}"] = sub.lower() in msg.lower()

    for sub in (case.get("must_not_contain") or []):
        checks[f"nao_contem:{sub[:20]}"] = sub.lower() not in msg.lower()

    if "expect_phase" in case:
        checks["fase"] = _fase(msg) == case["expect_phase"]

    return {"passed": all(checks.values()) if checks else True, "checks": checks}


def summarize(results: list[dict]) -> dict:
    """Sumariza uma lista de resultados {case, passed, checks}."""
    total = len(results)
    passou = sum(1 for r in results if r["passed"])
    return {
        "total": total,
        "passed": passou,
        "failed": total - passou,
        "accuracy": round(passou / total, 3) if total else 0.0,
    }
