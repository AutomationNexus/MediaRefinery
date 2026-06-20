"""MediaRefinery service layer (FastAPI).

This module is import-safe without the ``[service]`` extra so core
pipeline imports keep working in lightweight development contexts.
"""

__all__ = ["app", "routers", "deps", "security", "scheduler", "audit", "models", "web"]
