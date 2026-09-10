"""Comparador de fornecedores — transforma os resultados dos evals em decisão.

Lê `evals/results-<provider>.json` (um por fornecedor, gerados por run_evals) e
escreve `evals/comparativo.md`, com tabela qualidade/latência/custo, matriz de
casos e uma recomendação calculada (não opinada).

Uso:
    python -m evals.comparar
    python -m evals.comparar --baseline deepseek --ficheiros results-a.json results-b.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

EVALS = Path(__file__).resolve().parent
BASELINE = "deepseek"


def carregar(caminhos: list[Path]) -> list[dict]:
    dados = []
    for c in sorted(caminhos):
        d = json.loads(c.read_text(encoding="utf-8"))
        d["_fonte"] = c.name
        dados.append(d)
    return dados


def _ordena(dados: list[dict], baseline: str) -> list[dict]:
    """Baseline primeiro, depois por accuracy desc, depois por custo asc."""
    return sorted(
        dados,
        key=lambda d: (
            d["summary"]["provider"] != baseline,
            -d["summary"]["accuracy"],
            d["summary"]["custo_usd"]["por_caso"],
        ),
    )


def _delta_qualidade(d: dict, base: dict) -> str:
    dif = d["summary"]["passed"] - base["summary"]["passed"]
    if dif == 0:
        return "= (igual)"
    return f"{dif:+d} caso(s)"


def _tabela(dados: list[dict], baseline: str) -> str:
    base = next(d for d in dados if d["summary"]["provider"] == baseline)
    linhas = [
        "| Fornecedor | Modelo | Região (dados) | Qualidade | Δ vs baseline | Latência p50 | p95 | Custo/caso | Custo/turno |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for d in _ordena(dados, baseline):
        s = d["summary"]
        linha = (
            f"| {s['provider']} | {s['modelo']} | {s['regiao']} | "
            f"{s['passed']}/{s['total']} ({s['accuracy']:.0%}) | {_delta_qualidade(d, base)} | "
            f"{s['latencia_ms']['p50']:.0f} ms | {s['latencia_ms']['p95']:.0f} ms | "
            f"${s['custo_usd']['por_caso']:.6f} | ${s['custo_usd']['por_turno']:.6f} |"
        )
        linhas.append(linha)
    return "\n".join(linhas)


def _por_caso(d: dict) -> dict:
    """Agrega os resultados de um fornecedor por caso (suporta N repetições)."""
    out: dict[str, dict] = {}
    for r in d["results"]:
        c = out.setdefault(r["case"], {"passou": 0, "total": 0, "resposta": "",
                                       "falhas": set()})
        c["total"] += 1
        if r["passed"]:
            c["passou"] += 1
        else:
            c["falhas"].update(n for n, ok in r["checks"].items() if not ok)
        c["resposta"] = r.get("resposta") or c["resposta"]
    return out


def _matriz(dados: list[dict]) -> str:
    """Uma linha por caso, uma coluna por fornecedor (taxa de sucesso com repetições)."""
    casos: list[str] = []
    agregados = {}
    for d in dados:
        pid = d["summary"]["provider"]
        agregados[pid] = _por_caso(d)
        for c in agregados[pid]:
            if c not in casos:
                casos.append(c)
    provedores = [d["summary"]["provider"] for d in dados]
    linhas = ["| Caso | " + " | ".join(provedores) + " |", "|---" * (len(provedores) + 1) + "|"]
    for caso in casos:
        marcas = []
        for pid in provedores:
            c = agregados[pid].get(caso)
            if c is None:
                marcas.append("—")
            else:
                icone = "✅" if c["passou"] == c["total"] else ("❌" if c["passou"] == 0 else "⚠️")
                marcas.append(f"{icone} {c['passou']}/{c['total']}")
        linhas.append(f"| `{caso}` | " + " | ".join(marcas) + " |")
    return "\n".join(linhas)


def _divergencias(dados: list[dict]) -> str:
    """Casos em que os fornecedores NÃO concordam — mostra o que cada um respondeu."""
    agregados = {d["summary"]["provider"]: _por_caso(d) for d in dados}
    casos: list[str] = []
    for agg in agregados.values():
        for c in agg:
            if c not in casos:
                casos.append(c)
    blocos = []
    for caso in casos:
        taxas = {pid: agg[caso] for pid, agg in agregados.items() if caso in agg}
        if len({(c["passou"] == c["total"]) for c in taxas.values()}) <= 1:
            continue
        linhas = [f"### `{caso}`", ""]
        for pid, c in taxas.items():
            estado = "✅" if c["passou"] == c["total"] else ("❌" if c["passou"] == 0 else "⚠️")
            falhas = ", ".join(sorted(c["falhas"]))
            linhas.append(f"- **{pid}** {estado} {c['passou']}/{c['total']}"
                          + (f" (falhou: {falhas})" if falhas else ""))
            resposta = (c["resposta"] or "").replace("\n", " ").strip()
            linhas.append(f"  > {resposta[:300]}")
        linhas.append("")
        blocos.append("\n".join(linhas))
    return "\n".join(blocos) if blocos else \
        "Nenhuma divergência: todos os fornecedores acertam nos mesmos casos."


def _falhas(dados: list[dict]) -> str:
    linhas = []
    for d in _ordena(dados, BASELINE):
        s = d["summary"]
        nomes = [c for c, v in _por_caso(d).items() if v["passou"] < v["total"]]
        detalhe = ", ".join(f"`{n}`" for n in nomes) if nomes else "nenhum ✅"
        linhas.append(f"- **{s['provider']}** ({s['passed']}/{s['total']}): {detalhe}")
        if nomes:
            agg = _por_caso(d)
            for n in nomes:
                v = agg[n]
                linhas.append(f"  - `{n}`: {v['passou']}/{v['total']}"
                              + (f" — falhou: {', '.join(sorted(v['falhas']))}" if v["falhas"] else ""))
    return "\n".join(linhas)


def _recomendacao(dados: list[dict], baseline: str) -> list[str]:
    """Recomendação baseada em números: UE, qualidade ≥ baseline−1 caso, latência e custo."""
    base = next((d for d in dados if d["summary"]["provider"] == baseline), None)
    if not base:
        return [f"⚠️ Sem resultados do baseline `{baseline}` — não é possível comparar."]
    alvo = base["summary"]
    # tolerância = 1 caso de qualidade (em fração de repetições)
    repeticoes = alvo.get("estabilidade", {}).get("repeticoes", 1) or 1
    tolerancia = repeticoes / alvo["total"] if alvo["total"] else 0.0
    notas = []
    candidatos = []
    for d in dados:
        s = d["summary"]
        if s["provider"] == baseline:
            continue
        candidatos.append({
            "id": s["provider"],
            "ue": "China" not in s["regiao"],
            "qualidade_ok": s["accuracy"] >= alvo["accuracy"] - tolerancia,
            "lento": s["latencia_ms"]["p50"] > alvo["latencia_ms"]["p50"] * 1.5,
            "custo": s["custo_usd"]["por_turno"],
            "custo_base": alvo["custo_usd"]["por_turno"],
            "accuracy": s["accuracy"],
        })
    viaveis = [c for c in candidatos if c["ue"] and c["qualidade_ok"] and not c["lento"]]
    if not candidatos:
        notas.append(
            "ℹ️ Ainda não há candidatos medidos — corre os evals em pelo menos um fornecedor UE "
            "(`--provider mistral` / `--provider ovhcloud`) e volta a gerar este comparativo."
        )
        return notas
    if viaveis:
        melhor = min(viaveis, key=lambda c: c["custo"])
        notas.append(
            f"✅ **{melhor['id']}** cumpre os critérios: dados em solo UE, qualidade "
            f"{melhor['accuracy']:.0%} (baseline {alvo['accuracy']:.0%}, tolerância "
            f"{tolerancia:.0%}) e custo ${melhor['custo']:.6f}/turno vs "
            f"${melhor['custo_base']:.6f}/turno do baseline."
        )
    for c in candidatos:
        if c in viaveis:
            continue
        razoes = []
        if not c["ue"]:
            razoes.append("dados fora da UE")
        if not c["qualidade_ok"]:
            razoes.append(f"qualidade {c['accuracy']:.0%} abaixo da tolerância")
        if c["lento"]:
            razoes.append("latência p50 > 50% do baseline")
        notas.append(f"❌ **{c['id']}** fora: " + "; ".join(razoes) + ".")
    if not viaveis:
        notas.append(
            "ℹ️ Nenhum candidato UE cumpre os critérios — a decisão exige aceitar perda de "
            "qualidade **ou** re-afinar o prompt para o novo modelo (e repetir os evals)."
        )
    notas.append(
        "⚠️ Critérios definidos *a priori*: qualidade ≥ baseline − 1 caso, latência p50 sem "
        "agravamento > 50%, dados em solo UE. Ajusta-os se quiseres."
    )
    notas.append(
        "⚠️ Os custos usam preços configurados (PRICE_IN_PER_M/PRICE_OUT_PER_M) — "
        "confirma-os na página oficial antes de decidir por custo."
    )
    return notas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", default=BASELINE, help="fornecedor de referência (default: deepseek)")
    ap.add_argument("--ficheiros", nargs="*", help="ficheiros de resultados (default: results-*.json)")
    ap.add_argument("--out", default=str(EVALS / "comparativo.md"))
    args = ap.parse_args()

    if args.ficheiros:
        caminhos = [Path(f) for f in args.ficheiros]
    else:
        caminhos = [p for p in EVALS.glob("results-*.json")]
    caminhos = [c for c in caminhos if c.exists()]
    if not caminhos:
        print("Sem ficheiros de resultados. Corre primeiro: python -m evals.run_evals --provider X")
        return 2

    dados = carregar(caminhos)
    if not any(d["summary"]["provider"] == args.baseline for d in dados):
        print(f"⚠️ Sem resultados do baseline '{args.baseline}' (tenho: "
              f"{', '.join(d['summary']['provider'] for d in dados)}).")

    agora = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    casos_distintos = len({r["case"] for r in dados[0]["results"]})
    repeticoes = dados[0]["summary"].get("estabilidade", {}).get("repeticoes", 1)
    texto = "\n".join([
        f"# Comparativo de fornecedores de LLM — Lia ({agora})",
        "",
        f"Dataset de evals: **{casos_distintos} casos** × **{repeticoes} repetição(ões)** "
        f"(ver `evals/dataset.json`). Gerado por `python -m evals.comparar` — números, não opiniões.",
        "",
        "## Resultados",
        "",
        _tabela(dados, args.baseline),
        "",
        "## Casos falhados",
        "",
        _falhas(dados),
        "",
        "## Matriz por caso",
        "",
        _matriz(dados),
        "",
        "## Casos onde divergem (o que cada um respondeu)",
        "",
        _divergencias(dados),
        "",
        "## Recomendação (calculada)",
        "",
        "\n".join(_recomendacao(dados, args.baseline)),
        "",
        "## Ficheiros de origem",
        "",
        "\n".join(f"- `evals/{d['_fonte']}`" for d in dados),
        "",
    ])
    Path(args.out).write_text(texto, encoding="utf-8")
    print(texto)
    print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
