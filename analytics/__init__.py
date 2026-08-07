"""
analytics — rozszerzona, deterministyczna analiza gotowości.

Warstwa obliczeniowa (Apple + Hevy + MFP) pod LLM. Deterministyczna:
LLM nigdy nie liczy, tylko interpretuje gotowy JSON z `run_analysis`.

Struktura:
    config/     centralna konfiguracja (stałe, okna, progi)
    validators/ walidacja wejściowych metryk
    exceptions/ domenowe wyjątki
    logging.py  strukturalne logowanie
    models.py   typowane modele Pydantic
    baseline/acwr/temperature/nutrition/readiness — algorytmy
    fetch_*     konwersja danych MCP -> serie analityczne
    run_analysis — orchestrator (wejście JSON -> wyjście JSON)
"""

from .logging import get_logger

__version__ = "0.1.0"

logger = get_logger(__name__)

__all__ = ["__version__", "get_logger"]
