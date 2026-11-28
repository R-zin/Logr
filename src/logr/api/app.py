"""The FastAPI application: JSON API under /api, optional built dashboard at /."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from starlette.routing import Match, Mount
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from logr import auth as auth_mod
from logr.auth import AuthConfig
from logr.notify import NotifierConfig

from . import feeds, hackathons, searches, sync, tracking
from .deps import csrf


class _StaticMount(Mount):
    """The dashboard at ``/``, which never claims ``/api`` paths: an unknown API
    path falls through to the router's JSON 404 (or 405) instead of the SPA."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        path = scope.get("path", "")
        if path == "/api" or path.startswith("/api/"):
            return Match.NONE, {}
        return super().matches(scope)


def create_app(
    db_path: str | Path,
    *,
    auth: AuthConfig | None = None,
    static_dir: str | Path | None = None,
    notifier: NotifierConfig | None = None,
) -> FastAPI:
    config = auth if auth is not None else AuthConfig.from_env()
    notifier_config = notifier if notifier is not None else NotifierConfig.from_env()
    # CSRF is an app-wide dependency so it covers every router, auth's included.
    app = FastAPI(title="Logr", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
                  dependencies=[Depends(csrf)])
    app.state.db_path = str(db_path)
    app.state.auth = config
    app.state.notifier = notifier_config
    auth_mod.ensure_users_table(str(db_path))

    app.include_router(auth_mod.router)
    app.include_router(hackathons.router)
    app.include_router(tracking.router)
    app.include_router(searches.router)
    app.include_router(sync.router)
    app.include_router(feeds.router)

    if static_dir is not None and Path(static_dir).is_dir():
        app.router.routes.append(_StaticMount("/", app=StaticFiles(directory=str(static_dir), html=True),
                                              name="static"))
    return app


def run(db_path: str | Path, host: str = "127.0.0.1", port: int = 8000,
        static_dir: str | Path | None = None) -> None:
    import uvicorn

    uvicorn.run(create_app(db_path, static_dir=static_dir), host=host, port=port)
