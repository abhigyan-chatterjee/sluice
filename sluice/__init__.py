"""Sluice implementation package.

Submodules are imported on demand by :mod:`hermes_plugins.sluice_search` (the repository-root
``__init__.py``) and by the CLI; importing this package must stay free of Hermes and
third-party imports so the plugin validates and tests can import it in a bare
interpreter.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
