"""Carregamento de configuração e renderização do prompt.

Separa o COMPORTAMENTO (dados) do CÓDIGO. O agente.py não sabe quem é o
cliente: lê um config.yaml e um template de prompt, e combina-os.

Uso:
    from config import load_config, render_prompt
    cfg = load_config()                      # do ficheiro indicado por AGENT_CONFIG
    prompt = render_prompt(cfg)              # prompt final, sem placeholders
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

# Raiz do projeto: tenta a pasta acima de agente/ e também o diretório atual
# (o container copia os ficheiros "flat" para /app e usa as env vars).
_HERE = Path(__file__).resolve().parent
_CANDIDATOS = [_HERE.parent, _HERE, Path.cwd()]


def _primeiro_existente(relativo: str) -> Path:
    for base in _CANDIDATOS:
        p = base / relativo
        if p.exists():
            return p
    return _HERE.parent / relativo


DEFAULT_CONFIG = _primeiro_existente("configs/config.yaml")
DEFAULT_PROMPT = _primeiro_existente("prompts/system_prompt.md")
ROOT = _HERE.parent

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_0-9]+)\}\}")

# Mapa: placeholder do prompt  ->  chave no config.yaml
PLACEHOLDER_MAP = {
    "NOME_DA_ASSISTENTE": "assistente",
    "NOME_DA_EMPRESA": "empresa",
    "NOME_DO_RESPONSAVEL": "responsavel",
    "CIDADE": "cidade",
    "PAÍS": "pais",
    "PAIS": "pais",
    "IDIOMA": "idioma",
    "IDIOMA_PROIBIDO": "idioma_proibido",
    "IDIOMA_NOTA": "idioma_nota",
    "TERMOS_OBRIGATORIOS": "termos_obrigatorios",
    "TERMOS_PROIBIDOS": "termos_proibidos",
    "N_LINHAS": "n_linhas",
    "VALORES_LINHA_NEGOCIO": "valores_linha_negocio",
    "SERVICO_ANCORA": "servico_ancora",
    "PRECO_ANCORA": "preco_ancora",
    "SCORE_ORCAMENTO_MINIMO": "score_orcamento_minimo",
    "LINKS_PORTFOLIO": "links_portfolio",
    "GRELHA_PRECOS": "grelha_precos",
    "LINHA_1_NOME": "linha_1_nome",
    "LINHA_1_DESCRICAO": "linha_1_descricao",
    "LINHA_1_SERVICOS": "linha_1_servicos",
    "LINHA_1_CLIENTE_TIPICO": "linha_1_cliente_tipico",
    "LINHA_1_CASO_REFERENCIA": "linha_1_caso_referencia",
    "LINHA_2_NOME": "linha_2_nome",
    "LINHA_2_DESCRICAO": "linha_2_descricao",
    "LINHA_2_DETALHES": "linha_2_detalhes",
    "LINHA_2_SERVICOS": "linha_2_servicos",
    "LINHA_2_CLIENTE_TIPICO": "linha_2_cliente_tipico",
    "LINHA_2_ARGUMENTO": "linha_2_argumento",
    "LINHA_2_REGRA_DIREITOS": "linha_2_regra_direitos",
    "LINHA_2_REGRA_TRANSPARENCIA": "linha_2_regra_transparencia",
    "ROTEAMENTO_LINHA_1": "roteamento_linha_1",
    "ROTEAMENTO_LINHA_2": "roteamento_linha_2",
    "OBJECOES_EXTRA": "objecoes_extra",
    "FORA_DO_CATALOGO": "fora_do_catalogo",
    "CASOS_FORA_CATALOGO": "casos_fora_catalogo",
    "EXEMPLO_DOR_LEAD": "exemplo_dor_lead",
    "EXEMPLO_OBJETIVO": "exemplo_objetivo",
}


def load_config(path: str | os.PathLike | None = None) -> dict:
    """Carrega o config.yaml (ou o indicado por AGENT_CONFIG)."""
    cfg_path = Path(path or os.environ.get("AGENT_CONFIG", DEFAULT_CONFIG))
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Config não encontrado em {cfg_path}. "
            f"Copia configs/config.example.yaml para configs/config.yaml e preenche."
        )
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_prompt_template(path: str | os.PathLike | None = None) -> str:
    """Carrega o template do prompt (ou o indicado por AGENT_PROMPT)."""
    p = Path(path or os.environ.get("AGENT_PROMPT", DEFAULT_PROMPT))
    return p.read_text(encoding="utf-8")


def missing_placeholders(cfg: dict, template: str | None = None) -> list[str]:
    """Devolve placeholders do template que não têm valor no config."""
    template = template if template is not None else load_prompt_template()
    found = set(_PLACEHOLDER_RE.findall(template))
    faltam = []
    for ph in sorted(found):
        key = PLACEHOLDER_MAP.get(ph)
        if key is None or cfg.get(key) in (None, ""):
            faltam.append(ph)
    return faltam


def render_prompt(cfg: dict, template: str | None = None, strict: bool = False) -> str:
    """Substitui os {{PLACEHOLDERS}} do template pelos valores do config.

    strict=True levanta erro se faltar algum placeholder (útil em testes/deploy).
    """
    template = template if template is not None else load_prompt_template()

    if strict:
        faltam = missing_placeholders(cfg, template)
        if faltam:
            raise ValueError(f"Config incompleto. Falta preencher: {', '.join(faltam)}")

    def _sub(m: re.Match) -> str:
        ph = m.group(1)
        key = PLACEHOLDER_MAP.get(ph)
        if key is None or key not in cfg:
            return m.group(0)
        val = cfg.get(key)
        return "" if val is None else str(val)

    return _PLACEHOLDER_RE.sub(_sub, template)
