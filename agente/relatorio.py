"""Relatório de resultados a partir dos traces.

Lê `traces/conversations.jsonl` e resume o que o agente fez: conversas, leads,
marcações, handoffs, custo e latência. É o que transforma "acho que funciona"
num número que se mostra a um cliente.

Uso:
    python relatorio.py                 # imprime o relatório
    python relatorio.py --dias 7        # últimos 7 dias
    python relatorio.py --telegram      # envia também para o Telegram do responsável
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACES = Path(os.environ.get("TRACES_DIR", ROOT / "traces")) / "conversations.jsonl"


def carregar(caminho: Path | None = None) -> list[dict]:
    caminho = caminho or TRACES
    if not caminho.exists():
        return []
    linhas = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            linhas.append(json.loads(linha))
        except Exception:
            continue
    return linhas


def gerar_relatorio(dias: int | None = None, caminho: Path | None = None) -> dict:
    """Agrega os traces num resumo."""
    registos = carregar(caminho)
    if dias:
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        filtrados = []
        for r in registos:
            try:
                if datetime.fromisoformat(r["ts"]) >= corte:
                    filtrados.append(r)
            except Exception:
                filtrados.append(r)
        registos = filtrados

    por_conversa: dict[str, dict] = {}
    for r in registos:
        num = r.get("numero", "?")
        c = por_conversa.setdefault(num, {
            "turnos": 0, "cost": 0.0, "lat": [], "qualificado": False,
            "handoff": False, "marcou": False, "primeiro_ts": r.get("ts"),
        })
        c["turnos"] += 1
        c["cost"] += r.get("cost_usd", 0) or 0
        if r.get("latency_ms"):
            c["lat"].append(r["latency_ms"])
        c["qualificado"] = c["qualificado"] or bool(r.get("qualificado"))
        c["handoff"] = c["handoff"] or bool(r.get("handoff") or r.get("quer_humano"))
        c["marcou"] = c["marcou"] or bool(r.get("agendamento_confirmado"))

    n = len(por_conversa)
    lat_media = ([x for c in por_conversa.values() for x in c["lat"]] or [0])
    return {
        "periodo_dias": dias,
        "turnos": len(registos),
        "conversas": n,
        "leads_qualificados": sum(1 for c in por_conversa.values() if c["qualificado"]),
        "handoffs": sum(1 for c in por_conversa.values() if c["handoff"]),
        "marcacoes": sum(1 for c in por_conversa.values() if c["marcou"]),
        "custo_total": round(sum(c["cost"] for c in por_conversa.values()), 4),
        "custo_medio_conversa": round(sum(c["cost"] for c in por_conversa.values()) / n, 4) if n else 0.0,
        "latencia_media_ms": round(sum(lat_media) / len(lat_media), 0),
    }


def formatar_relatorio(d: dict) -> str:
    periodo = f"últimos {d['periodo_dias']} dias" if d.get("periodo_dias") else "todo o histórico"
    if not d["conversas"]:
        return f"📊 Relatório ({periodo})\n\nSem conversas registadas ainda."
    taxa_marc = d["marcacoes"] / d["conversas"] * 100
    taxa_qual = d["leads_qualificados"] / d["conversas"] * 100
    return (
        f"📊 Relatório ({periodo})\n\n"
        f"Conversas: {d['conversas']}  ·  Turnos: {d['turnos']}\n"
        f"Leads qualificados: {d['leads_qualificados']} ({taxa_qual:.0f}%)\n"
        f"Reuniões marcadas: {d['marcacoes']} ({taxa_marc:.0f}%)\n"
        f"Handoffs para humano: {d['handoffs']}\n\n"
        f"Custo total: ${d['custo_total']:.4f}\n"
        f"Custo médio por conversa: ${d['custo_medio_conversa']:.4f}\n"
        f"Latência média por turno: {d['latencia_media_ms']:.0f} ms"
    )


def enviar_telegram(texto: str):
    import httpx
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("⚠️ TELEGRAM_TOKEN/CHAT_ID ausentes — não enviei.")
        return
    httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
               json={"chat_id": chat, "text": texto}, timeout=15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=None)
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()
    texto = formatar_relatorio(gerar_relatorio(dias=args.dias))
    print(texto)
    if args.telegram:
        enviar_telegram(texto)
        print("\n(enviado para o Telegram)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
