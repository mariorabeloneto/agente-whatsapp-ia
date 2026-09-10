"""Runner de evals — corre o dataset de conversas contra o agente e pontua.

Uso:
    python -m evals.run_evals            # corre todos os casos (precisa de chave LLM)
    python -m evals.run_evals --case pedido_humano
    python -m evals.run_evals --dry      # só valida o dataset/scorer, sem chamar o LLM

Saída: tabela no terminal + evals/results.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agente"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorer import score_case, summarize  # noqa: E402

DATASET = Path(__file__).resolve().parent / "dataset.json"
RESULTS = Path(__file__).resolve().parent / "results.json"


def _extrair_metadata(msg: str) -> dict:
    m = re.search(r"<METADATA>(.*?)</METADATA>", msg or "", re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return {}


async def _correr_caso(case: dict, agente_mod) -> dict:
    numero = f"3519000000{abs(hash(case['id'])) % 10000}@s.whatsapp.net"
    ultimo_msg, ultimo_md = "", {}
    for turno in case["turns"]:
        resposta = await agente_mod.obter_resposta_agente(numero, turno)
        msg, md = resposta[0], resposta[1] if len(resposta) > 1 else None
        ultimo_msg, ultimo_md = msg, (md or ultimo_md)
    tools = agente_mod._ferramentas_usadas(ultimo_md)
    result = {"message": ultimo_msg, "metadata": ultimo_md, "tools": tools}
    return score_case(case, result)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="correr só um caso (id)")
    ap.add_argument("--dry", action="store_true", help="sem chamar o LLM")
    args = ap.parse_args()

    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"caso '{args.case}' não encontrado")
            return 2

    if args.dry:
        # valida estrutura do dataset
        faltas = [c["id"] for c in cases if "turns" not in c or not c["turns"]]
        print(f"dataset: {len(cases)} casos, {len(faltas)} inválidos")
        return 1 if faltas else 0

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("⚠️  DEEPSEEK_API_KEY não definida — usa --dry ou define a chave.")
        return 2

    import agente as agente_mod

    resultados = []
    for c in cases:
        r = await _correr_caso(c, agente_mod)
        resultados.append({"case": c["id"], **r})
        mark = "✅" if r["passed"] else "❌"
        print(f"{mark} {c['id']}")
        if not r["passed"]:
            for nome, ok in r["checks"].items():
                if not ok:
                    print(f"      falhou: {nome}")

    resumo = summarize(resultados)
    print(f"\n{resumo['passed']}/{resumo['total']} passaram "
          f"(accuracy {resumo['accuracy']})")

    RESULTS.write_text(
        json.dumps({"summary": resumo, "results": resultados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if resumo["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
