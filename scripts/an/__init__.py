"""Deep analysis and scoring layer.

Everything new lives under this package so the original pipeline in ``scripts/``
keeps working untouched. Nothing here imports ``config.py`` at module import time,
so the tests run without creating directories or touching the network.
"""

__all__ = ["paths", "store", "local", "research_md", "journal", "model", "metrics"]
