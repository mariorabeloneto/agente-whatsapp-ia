"""Persistência do histórico de conversas.

O histórico vivia num `dict` em memória — um restart do container perdia todas
as conversas a meio e impedia correr mais de uma réplica. Este módulo guarda-o
no Redis (que já faz parte do stack da Evolution), com fallback para memória
quando não há Redis disponível (dev/testes).

Uso:
    store = HistoricoStore(redis_url, max_len=20, ttl=7*24*3600)
    hist = await store.get(numero)
    await store.append(numero, "user", "olá")
    await store.set(numero, hist)
    n = await store.count()
"""
from __future__ import annotations

import asyncio
import json

try:
    import redis.asyncio as aioredis
except Exception:  # redis é opcional em ambiente de teste
    aioredis = None

_PREFIX = "agente:hist:"


class HistoricoStore:
    """Histórico de conversas por número, persistido no Redis com fallback local."""

    def __init__(self, redis_url: str | None = None, max_len: int = 20,
                 ttl: int = 7 * 24 * 3600):
        self.max_len = max_len
        self.ttl = ttl
        self._mem: dict[str, list] = {}
        self._redis = None
        self._redis_url = redis_url
        if redis_url and aioredis is not None:
            try:
                self._redis = aioredis.from_url(redis_url, decode_responses=True)
            except Exception as e:
                print(f"⚠️ memory: Redis indisponível, uso memória ({e})")

    @property
    def persistente(self) -> bool:
        return self._redis is not None

    def _key(self, numero: str) -> str:
        return _PREFIX + numero

    async def get(self, numero: str) -> list:
        if self._redis is not None:
            try:
                raw = await self._redis.get(self._key(numero))
                return json.loads(raw) if raw else []
            except Exception as e:
                print(f"⚠️ memory.get falhou, uso memória ({e})")
        return list(self._mem.get(numero, []))

    async def set(self, numero: str, lista: list) -> None:
        if len(lista) > self.max_len:
            lista = lista[-self.max_len:]
        self._mem[numero] = lista
        if self._redis is not None:
            try:
                await self._redis.set(self._key(numero), json.dumps(lista, ensure_ascii=False),
                                      ex=self.ttl)
            except Exception as e:
                print(f"⚠️ memory.set falhou ({e})")

    async def append(self, numero: str, role: str, content: str) -> list:
        lista = await self.get(numero)
        lista.append({"role": role, "content": content})
        if len(lista) > self.max_len:
            lista = lista[-self.max_len:]
        await self.set(numero, lista)
        return lista

    async def clear(self, numero: str) -> None:
        self._mem.pop(numero, None)
        if self._redis is not None:
            try:
                await self._redis.delete(self._key(numero))
            except Exception:
                pass

    async def count(self) -> int:
        if self._redis is not None:
            try:
                n = 0
                async for _ in self._redis.scan_iter(match=_PREFIX + "*"):
                    n += 1
                return n
            except Exception:
                pass
        return len(self._mem)
