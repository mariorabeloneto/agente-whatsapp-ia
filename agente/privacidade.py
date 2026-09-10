"""Apagamento de dados de um titular (direito ao esquecimento / RGPD art. 17).

Apaga os dados de um número em TODOS os sistemas a que o agente tem acesso:
  - Redis (histórico de conversa)
  - Airtable (registo do lead)
  - Google Calendar (eventos criados com esse contacto)

As mensagens guardadas na Evolution API (Postgres) são apagadas em separado pelo
script `ops/apagar_dados.sh`, que corre no servidor.

Uso (dentro do container):
    python privacidade.py 351913326279
    python privacidade.py 351913326279 --dry   # só mostra o que apagaria
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx


def _jid(numero: str) -> str:
    n = numero.replace("@s.whatsapp.net", "").strip()
    return n + "@s.whatsapp.net"


def _so_digitos(numero: str) -> str:
    return "".join(ch for ch in numero if ch.isdigit())


async def apagar_redis(numero: str, dry: bool) -> int:
    import agente
    jid = _jid(numero)
    hist = await agente.historico.get(jid)
    if dry:
        return 1 if hist else 0
    await agente.historico.clear(jid)
    return 1 if hist else 0


async def apagar_airtable(numero: str, dry: bool) -> int:
    import agente
    token, base, tabela = agente.AIRTABLE_TOKEN, agente.AIRTABLE_BASE, agente.AIRTABLE_TABLE
    if not (token and base and tabela):
        return 0
    alvo = _so_digitos(numero)
    url = f"https://api.airtable.com/v0/{base}/{tabela}"
    headers = {"Authorization": f"Bearer {token}"}
    apagados = 0
    async with httpx.AsyncClient() as c:
        r = await c.get(url, headers=headers, params={"maxRecords": 100}, timeout=20)
        for rec in r.json().get("records", []):
            tel = _so_digitos(str(rec.get("fields", {}).get("Telefone", "")))
            if tel and (tel == alvo or tel.endswith(alvo) or alvo.endswith(tel)):
                if not dry:
                    await c.delete(f"{url}/{rec['id']}", headers=headers, timeout=20)
                apagados += 1
    return apagados


def apagar_calendar(numero: str, dry: bool) -> int:
    import agente
    alvo = _so_digitos(numero)
    svc = agente.get_calendar_service()
    apagados = 0
    evs = svc.events().list(calendarId="primary", maxResults=250, singleEvents=True).execute()
    for e in evs.get("items", []):
        texto = f"{e.get('summary','')} {e.get('description','')}"
        if alvo and alvo in _so_digitos(texto):
            if not dry:
                svc.events().delete(calendarId="primary", eventId=e["id"]).execute()
            apagados += 1
    return apagados


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("numero")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    n = args.numero
    print(f"{'[DRY-RUN] ' if args.dry else ''}Apagar dados de {n}")
    try:
        r = await apagar_redis(n, args.dry)
        print(f"  Redis (histórico): {r}")
    except Exception as e:
        print(f"  Redis: erro {e}")
    try:
        a = await apagar_airtable(n, args.dry)
        print(f"  Airtable (lead): {a}")
    except Exception as e:
        print(f"  Airtable: erro {e}")
    try:
        c = apagar_calendar(n, args.dry)
        print(f"  Calendar (eventos): {c}")
    except Exception as e:
        print(f"  Calendar: erro {e}")
    print("  Evolution (mensagens): usar ops/apagar_dados.sh no servidor")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
