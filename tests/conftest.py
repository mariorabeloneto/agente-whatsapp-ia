"""Configuração comum dos testes: garante que agente/ e evals/ são importáveis."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("agente", "evals"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
