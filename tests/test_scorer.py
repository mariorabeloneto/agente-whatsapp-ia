"""Testes do scorer de evals."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "evals"))

from scorer import score_case, summarize  # noqa: E402


def _res(**kw):
    base = {"message": "", "metadata": {}, "tools": []}
    base.update(kw)
    return base


def test_ferramenta_esperada_presente():
    case = {"expect_tools": ["telegram.request_human"]}
    assert score_case(case, _res(tools=["telegram.request_human"]))["passed"]


def test_ferramenta_esperada_ausente():
    case = {"expect_tools": ["telegram.request_human"]}
    assert not score_case(case, _res(tools=[]))["passed"]


def test_metadata_esperado():
    case = {"expect_metadata": {"quer_humano": True}}
    assert score_case(case, _res(metadata={"quer_humano": True}))["passed"]
    assert not score_case(case, _res(metadata={"quer_humano": False}))["passed"]


def test_must_contain_e_not_contain():
    case = {"must_contain": ["responsável"], "must_not_contain": ["desconto"]}
    assert score_case(case, _res(message="falo com o responsável"))["passed"]
    assert not score_case(case, _res(message="dou-te um desconto"))["passed"]


def test_deteccao_de_fase():
    case = {"expect_phase": "abertura"}
    msg = "Olá! Sou a X, assistente virtual da Y. Em que posso ajudar?"
    assert score_case(case, _res(message=msg))["passed"]


def test_summarize():
    res = summarize([{"passed": True}, {"passed": False}, {"passed": True}])
    assert res == {"total": 3, "passed": 2, "failed": 1, "accuracy": 0.667}
