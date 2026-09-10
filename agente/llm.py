"""Abstração de fornecedor de LLM — trocar de fornecedor é CONFIGURAÇÃO, não código.

Todos os fornecedores suportados falam a API compatível com OpenAI
(`POST {url}/chat/completions`), por isso o agente nunca precisa de saber qual está
em uso: pergunta a este módulo o url, a chave e o modelo. Trocar de fornecedor em
produção = mudar 1-2 linhas no `.env` e reiniciar o container.

Variáveis de ambiente (todas opcionais — o que não for definido usa o default do
fornecedor escolhido):

    LLM_PROVIDER   deepseek (omissão) | mistral | ovhcloud
    LLM_API_KEY    chave do fornecedor (alternativa: a env específica do fornecedor,
                   ex. MISTRAL_API_KEY / OVHCLOUD_API_KEY / DEEPSEEK_API_KEY)
    LLM_MODEL      nome do modelo
    LLM_URL        base URL da API
    PRICE_IN_PER_M / PRICE_OUT_PER_M   preços em USD por 1M tokens, usados para
                   estimar custo nos traces e nos evals

Uso:
    from llm import LLM
    LLM.modelo, LLM.url, LLM.api_key, LLM.preco_in, LLM.preco_out
    LLM.resumo()          # linha para logs/health — nunca inclui a chave
    LLM.tem_chave         # True se há chave configurada

A chave NUNCA é impressa (nem por `resumo()`, nem por `repr()`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

PADRAO = "deepseek"


@dataclass(frozen=True)
class Provider:
    """Definição de um fornecedor de LLM (compatível com a API da OpenAI)."""

    id: str
    nome: str
    url: str
    modelo: str
    chave_env: str
    regiao: str
    rgpd: str
    preco_in: float          # USD por 1M tokens de entrada
    preco_out: float         # USD por 1M tokens de saída
    # Envs específicas do fornecedor (compatibilidade com configurações antigas,
    # ex. DEEPSEEK_MODEL/DEEPSEEK_URL): só usadas se LLM_MODEL/LLM_URL não existirem.
    modelo_env: str = ""
    url_env: str = ""
    chave: str = field(default="", repr=False)

    @property
    def api_key(self) -> str:
        return self.chave

    @property
    def tem_chave(self) -> bool:
        return bool(self.chave)

    def resumo(self) -> str:
        """Descrição curta e sem segredos, para logs e /health."""
        estado = "chave ok" if self.tem_chave else f"sem {self.chave_env}"
        return f"{self.id} ({self.nome}) modelo={self.modelo} [{estado}]"


# Preços: valores públicos aproximados, por 1M tokens em USD. NÃO são fonte de
# verdade — confirma-os na página oficial do fornecedor antes de decidir por custo
# e, se preciso, sobrepõe com PRICE_IN_PER_M / PRICE_OUT_PER_M no `.env`.
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider(
        id="deepseek",
        nome="DeepSeek (China)",
        url="https://api.deepseek.com/v1",
        modelo="deepseek-chat",
        chave_env="DEEPSEEK_API_KEY",
        regiao="China",
        rgpd="SEM decisão de adequação da UE → transferência exige SCC + avaliação de risco",
        preco_in=0.27,
        preco_out=1.10,
        modelo_env="DEEPSEEK_MODEL",
        url_env="DEEPSEEK_URL",
    ),
    "mistral": Provider(
        id="mistral",
        nome="Mistral AI (França, UE)",
        url="https://api.mistral.ai/v1",
        modelo="mistral-small-latest",
        chave_env="MISTRAL_API_KEY",
        regiao="França (UE)",
        rgpd="Tratamento na UE, RGPD por defeito; DPA no site da Mistral",
        preco_in=0.10,
        preco_out=0.30,
    ),
    "ovhcloud": Provider(
        id="ovhcloud",
        nome="OVHcloud AI Endpoints (UE)",
        url="https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        modelo="Mistral-Small-3.2-24B-Instruct-2506",
        chave_env="OVHCLOUD_API_KEY",
        regiao="UE (datacenters OVHcloud, França)",
        rgpd="Inferência em solo UE, RGPD por defeito",
        preco_in=0.0,
        preco_out=0.0,
    ),
}


def provedores() -> list[str]:
    """IDs dos fornecedores suportados."""
    return list(PROVIDERS)


def resolver(provider_id: str | None = None, *, env: dict | None = None) -> Provider:
    """Resolve o fornecedor ativo a partir do ambiente (ou de `env` explícito)."""
    e = os.environ if env is None else env
    pid = (provider_id or e.get("LLM_PROVIDER") or PADRAO).strip().lower()
    if pid not in PROVIDERS:
        raise ValueError(
            f"LLM_PROVIDER desconhecido: {pid!r}. Opções: {', '.join(PROVIDERS)}"
        )
    base = PROVIDERS[pid]
    return replace(
        base,
        url=e.get("LLM_URL") or (e.get(base.url_env) if base.url_env else None) or base.url,
        modelo=e.get("LLM_MODEL") or (e.get(base.modelo_env) if base.modelo_env else None) or base.modelo,
        chave=e.get("LLM_API_KEY") or e.get(base.chave_env) or "",
        preco_in=float(e.get("PRICE_IN_PER_M") or base.preco_in),
        preco_out=float(e.get("PRICE_OUT_PER_M") or base.preco_out),
    )


# Fornecedor ativo neste processo (resolvido no import, como o resto da config).
LLM = resolver()
