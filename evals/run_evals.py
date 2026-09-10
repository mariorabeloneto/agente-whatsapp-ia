"""Runner de evals — corre o dataset contra UM fornecedor de LLM e pontua.

Uso:
    python -m evals.run_evals --provider deepseek
    python -m evals.run_evals --provider mistral --out evals/results-mistral.json
    python -m evals.run_evals --case pedido_humano
    python -m evals.run_evals --dry          # valida o dataset, sem chamar o LLM

Um fornecedor por processo (a config do agente é resolvida no import), por isso
comparar fornecedores = correr este runner uma vez por cada e depois:

    python -m evals.comparar                 # gera evals/comparativo.md

Chave: vem do ambiente ou de um ficheiro `.env` (por omissão `agente/.env`);
`--provider mistral` exige `MISTRAL_API_KEY` (ou `LLM_API_KEY`).

Saída: tabela no terminal + evals/results-<provider>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agente"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorer import score_case, summarize  # noqa: E402

DATASET = Path(__file__).resolve().parent / "dataset.json"


def _extrair_metadata(msg: str) -> dict:
    m = re.search(r"<METADATA>(.*?)</METADATA>", msg or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return {}


def _carregar_env(caminho: str | None) -> str | None:
    """Carrega o .env (sem sobrepor variáveis já definidas no ambiente)."""
    if caminho:
        p = Path(caminho).expanduser()
    else:
        p = Path(os.environ.get("EVALS_ENV_FILE") or ROOT / "agente" / ".env")
    if not p.exists():
        return None
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return None
    load_dotenv(p, override=False)
    return str(p)


async def _correr_caso(case: dict, agente_mod) -> dict:
    # Número único por corrida: garante isolamento (o histórico é persistente)
    numero = f"evals-{int(time.time())}-{case['id']}@s.whatsapp.net"
    try:
        await agente_mod.historico.clear(numero)
    except Exception:
        pass
    ultimo_msg, ultimo_md = "", {}
    uso_total = {"prompt_tokens": 0, "completion_tokens": 0}
    latencias = []
    for turno in case["turns"]:
        t0 = time.perf_counter()
        resposta = await agente_mod.obter_resposta_agente(numero, turno)
        latencias.append((time.perf_counter() - t0) * 1000)
        msg = resposta[0]
        md = resposta[1] if len(resposta) > 1 else None
        uso = resposta[2] if len(resposta) > 2 else {}
        ultimo_msg, ultimo_md = msg, (md or ultimo_md)
        uso_total["prompt_tokens"] += (uso or {}).get("prompt_tokens", 0)
        uso_total["completion_tokens"] += (uso or {}).get("completion_tokens", 0)
    tools = agente_mod._ferramentas_usadas(ultimo_md)
    result = {"message": ultimo_msg, "metadata": ultimo_md, "tools": tools}
    pontuacao = score_case(case, result)
    return {
        **pontuacao,
        "resposta": (ultimo_msg or "")[:500],   # evidência para ler o comparativo
        "latencia_ms": round(sum(latencias), 1),
        "latencias_turno_ms": [round(x, 1) for x in latencias],
        "prompt_tokens": uso_total["prompt_tokens"],
        "completion_tokens": uso_total["completion_tokens"],
    }


def _percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    if len(ordenados) == 1:
        return ordenados[0]
    k = (len(ordenados) - 1) * p
    f, c = int(k), min(int(k) + 1, len(ordenados) - 1)
    return ordenados[f] + (ordenados[c] - ordenados[f]) * (k - f)


def _resumo_metricas(resultados: list[dict], llm) -> dict:
    base = summarize([{"passed": r["passed"], "checks": r["checks"]} for r in resultados])
    lat = [r["latencia_ms"] for r in resultados]
    p_in = sum(r["prompt_tokens"] for r in resultados)
    p_out = sum(r["completion_tokens"] for r in resultados)
    custo = (p_in / 1e6) * llm.preco_in + (p_out / 1e6) * llm.preco_out
    turnos = sum(len(r["latencias_turno_ms"]) for r in resultados)

    # Estabilidade por caso (com repetições, o mesmo caso pode passar nuns turnos e não noutros)
    por_caso: dict[str, list[bool]] = {}
    for r in resultados:
        por_caso.setdefault(r["case"], []).append(bool(r["passed"]))
    repeticoes = max((len(v) for v in por_caso.values()), default=1)
    estabilidade = {
        "repeticoes": repeticoes,
        "sempre": [c for c, v in por_caso.items() if all(v)],
        "instavel": [c for c, v in por_caso.items() if any(v) and not all(v)],
        "nunca": [c for c, v in por_caso.items() if not any(v)],
    }

    return {
        **base,
        "provider": llm.id,
        "modelo": llm.modelo,
        "regiao": llm.regiao,
        "estabilidade": estabilidade,
        "latencia_ms": {
            "media": round(statistics.fmean(lat), 1) if lat else 0.0,
            "p50": round(_percentil(lat, 0.50), 1),
            "p95": round(_percentil(lat, 0.95), 1),
            "max": round(max(lat), 1) if lat else 0.0,
        },
        "tokens": {"prompt": p_in, "completion": p_out, "total": p_in + p_out,
                   "por_turno": round((p_in + p_out) / turnos, 1) if turnos else 0.0},
        "custo_usd": {
            "total": round(custo, 6),
            "por_caso": round(custo / len(resultados), 6) if resultados else 0.0,
            "por_turno": round(custo / turnos, 6) if turnos else 0.0,
            "preco_in": llm.preco_in,
            "preco_out": llm.preco_out,
        },
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="correr só um caso (id)")
    ap.add_argument("--provider", help="fornecedor de LLM (deepseek|mistral|ovhcloud)")
    ap.add_argument("--repeticoes", type=int, default=1,
                    help="repetições por caso (default 1; usar 3 no comparativo — o modelo varia entre corridas)")
    ap.add_argument("--out", help="ficheiro de resultados (default: evals/results-<provider>.json)")
    ap.add_argument("--env-file", help="ficheiro .env com as chaves (default: agente/.env)")
    ap.add_argument("--dry", action="store_true", help="sem chamar o LLM")
    args = ap.parse_args()

    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"caso '{args.case}' não encontrado")
            return 2

    if args.dry:
        faltas = [c["id"] for c in cases if "turns" not in c or not c["turns"]]
        print(f"dataset: {len(cases)} casos, {len(faltas)} inválidos")
        return 1 if faltas else 0

    # 1) Fornecedor escolhido ANTES de importar o agente (a config resolve-se no import)
    env_usado = _carregar_env(args.env_file)
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider

    from llm import LLM  # noqa: E402  (depois de fixar LLM_PROVIDER)

    if not LLM.tem_chave:
        print(f"⚠️  {LLM.chave_env} não definida (nem LLM_API_KEY) — "
              f"define-a no ambiente ou em {env_usado or 'agente/.env'}.")
        return 2

    print(f"🔌 {LLM.resumo()}")
    if env_usado:
        print(f"   .env: {env_usado}")

    import agente as agente_mod  # noqa: E402  (importa isto só depois da config fixada)

    resultados = []
    for rep in range(max(1, args.repeticoes)):
        if args.repeticoes > 1:
            print(f"— repetição {rep + 1}/{args.repeticoes} —")
        for c in cases:
            r = await _correr_caso(c, agente_mod)
            resultados.append({"case": c["id"], "rep": rep + 1, **r})
            mark = "✅" if r["passed"] else "❌"
            print(f"{mark} {c['id']}  ({r['latencia_ms']:.0f} ms)")
            if not r["passed"]:
                for nome, ok in r["checks"].items():
                    if not ok:
                        print(f"      falhou: {nome}")

    resumo = _resumo_metricas(resultados, LLM)
    print(f"\n{resumo['passed']}/{resumo['total']} passaram (accuracy {resumo['accuracy']})")
    est = resumo["estabilidade"]
    if est["repeticoes"] > 1:
        print(f"estabilidade ({est['repeticoes']} reps): sempre {len(est['sempre'])} | "
              f"instável {est['instavel'] or '—'} | nunca {est['nunca'] or '—'}")
    print(f"latência: p50 {resumo['latencia_ms']['p50']} ms | "
          f"p95 {resumo['latencia_ms']['p95']} ms | média {resumo['latencia_ms']['media']} ms")
    print(f"tokens: {resumo['tokens']['total']} | custo estimado: "
          f"${resumo['custo_usd']['total']} (${resumo['custo_usd']['por_turno']}/turno)")

    destino = Path(args.out) if args.out else (
        Path(__file__).resolve().parent / f"results-{LLM.id}.json")
    destino.write_text(
        json.dumps({"summary": resumo, "results": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"→ {destino}")
    return 0 if resumo["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
