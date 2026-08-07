"""
logging.py — konfiguracja strukturalnego logowania pakietu analytics.

Domyślnie logi trafiają na stderr z formatem zawierającym poziom, logger,
czas i komunikat. Flaga `ANALYTICS_DEBUG=1` włącza DEBUG (szczegóły obliczeń).

Moduły logują przez `logger = get_logger(__name__)` — struktura wiadomości
pozwala później łatwo dodać JSON-output / centralny collector bez zmiany
miejsc wywołań.
"""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False
_LEVEL = logging.INFO


def _configure() -> None:
    global _CONFIGURED, _LEVEL
    if _CONFIGURED:
        return
    _LEVEL = logging.DEBUG if os.environ.get("ANALYTICS_DEBUG") in ("1", "true", "yes") else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger("analytics")
    root.setLevel(_LEVEL)
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Zwraca logger podpięty pod drzewo `analytics` (raz skonfigurowane)."""
    _configure()
    return logging.getLogger(f"analytics.{name}")


def debug_enabled() -> bool:
    return _LEVEL == logging.DEBUG


__all__ = ["get_logger", "debug_enabled"]
