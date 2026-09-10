"""Testes da abstração de fornecedor de LLM (troca por configuração)."""
import pytest

from llm import PROVIDERS, PADRAO, Provider, provedores, resolver


def test_fornecedores_suportados():
    assert PADRAO in PROVIDERS
    assert "mistral" in provedores()
    assert "ovhcloud" in provedores()


def test_default_e_deepseek():
    p = resolver(env={})
    assert p.id == PADRAO
    assert p.modelo == PROVIDERS[PADRAO].modelo
    assert p.tem_chave is False


def test_escolher_fornecedor_por_env():
    p = resolver(env={"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "x"})
    assert p.id == "mistral"
    assert p.url == PROVIDERS["mistral"].url
    assert p.tem_chave is True


def test_chave_generica_tem_prioridade():
    p = resolver(env={"LLM_PROVIDER": "ovhcloud", "OVHCLOUD_API_KEY": "especifica",
                      "LLM_API_KEY": "generica"})
    assert p.api_key == "generica"


def test_overrides_de_url_modelo_e_preco():
    p = resolver(env={
        "LLM_PROVIDER": "deepseek",
        "LLM_URL": "https://exemplo/v1",
        "LLM_MODEL": "modelo-x",
        "PRICE_IN_PER_M": "1.5",
        "PRICE_OUT_PER_M": "3",
    })
    assert (p.url, p.modelo, p.preco_in, p.preco_out) == ("https://exemplo/v1", "modelo-x", 1.5, 3.0)


def test_fornecedor_invalido_falha_claramente():
    with pytest.raises(ValueError):
        resolver(env={"LLM_PROVIDER": "openai"})


def test_resumo_nao_revela_chave():
    p = resolver(env={"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "segredo-123"})
    assert "segredo-123" not in p.resumo()
    assert "segredo-123" not in repr(p)


def test_todos_os_fornecedores_falam_api_openai():
    for p in PROVIDERS.values():
        assert isinstance(p, Provider)
        assert p.url.startswith("https://")
        assert p.chave_env
