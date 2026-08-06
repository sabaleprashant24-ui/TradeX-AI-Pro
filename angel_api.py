"""
Compatibility shim for legacy/import paths that expect `angel_api.py`.
This preserves backward compatibility with the existing `ANGEL_API` singleton
implemented in `angel_login.py` without changing the live trading architecture.
"""

from angel_login import ANGEL_API, angel_api

__all__ = ["ANGEL_API", "angel_api"]
