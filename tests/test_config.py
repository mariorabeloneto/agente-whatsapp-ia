"""Testes do carregamento de configuração e renderização do prompt."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agente"))

import config  # noqa: E402


def test_load_config_example():
    cfg = config.load_config(ROOT / "configs" / "config.example.yaml")
    assert cfg["assistente"]
    assert "horarios" in cfg
    assert cfg["horarios"]["janelas"]


def test_render_substitui_placeholders():
    cfg = config.load_config(ROOT / "configs" / "config.example.yaml")
    out = config.render_prompt(cfg)
    assert "{{" not in out, "ficaram placeholders por substituir"
    assert cfg["assistente"] in out
    assert cfg["empresa"] in out


def test_placeholder_vazio_e_removido():
    cfg = {"assistente": "X", "objecoes_extra": ""}
    out = config.render_prompt(cfg, template="a {{OBJECOES_EXTRA}} b", strict=False)
    assert "{{" not in out


def test_chave_ausente_mantem_placeholder():
    out = config.render_prompt({}, template="a {{NOME_DA_EMPRESA}} b", strict=False)
    assert "{{NOME_DA_EMPRESA}}" in out


def test_strict_levanta_com_config_incompleto():
    with pytest.raises(ValueError):
        config.render_prompt({}, template="{{NOME_DA_EMPRESA}}", strict=True)


def test_missing_placeholders():
    faltam = config.missing_placeholders({}, template="{{NOME_DA_EMPRESA}} {{CIDADE}}")
    assert "NOME_DA_EMPRESA" in faltam and "CIDADE" in faltam


def test_config_inexistente_levanta():
    with pytest.raises(FileNotFoundError):
        config.load_config("/caminho/que/nao/existe.yaml")
