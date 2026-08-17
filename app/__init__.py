"""Application package.

Deliberately light: no heavy imports at module scope. Importing `app.auth.tokens`
must not require Redis to be installed or reachable — otherwise unit tests and
tooling need the full runtime just to read a module. The factory lives in
`app.factory`.
"""
from __future__ import annotations

__all__ = ["create_app"]


def create_app():
    # Imported lazily so this package can be imported without the full stack.
    from app.factory import create_app as _create_app
    return _create_app()
