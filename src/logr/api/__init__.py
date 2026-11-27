"""Logr HTTP API (docs/API.md). ``create_app`` builds the FastAPI app; ``run`` serves it."""

from logr.api.app import create_app, run

__all__ = ["create_app", "run"]
