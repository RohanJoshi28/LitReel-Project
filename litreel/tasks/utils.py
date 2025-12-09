from __future__ import annotations

from flask import current_app


def ensure_app_context():
    """Return (app, ctx) ensuring a Flask application context is available."""
    try:
        app = current_app._get_current_object()  # type: ignore[attr-defined]
        return app, None
    except RuntimeError:
        from .. import create_app

        app = create_app()
        ctx = app.app_context()
        ctx.push()
        return app, ctx


__all__ = ["ensure_app_context"]
