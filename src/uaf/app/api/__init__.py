"""FastAPI app factory and route registration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from uaf.app.api.routes_artifacts import router as artifacts_router
from uaf.app.api.routes_auth import router as auth_router
from uaf.app.api.routes_import_export import router as import_export_router
from uaf.app.api.routes_lens import router as lens_router
from uaf.app.api.routes_nodes import router as nodes_router
from uaf.app.api.routes_nodes import search_router
from uaf.app.api.routes_sharing import router as sharing_router
from uaf.app.frontend.routes import router as frontend_router

if TYPE_CHECKING:
    from uaf.app.lenses import LensRegistry
    from uaf.security.secure_graph_db import SecureGraphDB

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(db: SecureGraphDB, registry: LensRegistry) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(title="UAF API", version="1.0")
    app.state.db = db
    app.state.registry = registry

    # API routes
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
    app.include_router(artifacts_router, prefix="/api/artifacts", tags=["artifacts"])
    app.include_router(nodes_router, prefix="/api/nodes", tags=["nodes"])
    app.include_router(lens_router, prefix="/api/artifacts", tags=["lens"])
    app.include_router(search_router, prefix="/api/search", tags=["search"])
    app.include_router(import_export_router, prefix="/api", tags=["import/export"])
    app.include_router(sharing_router, prefix="/api/sharing", tags=["sharing"])

    # Static files and frontend
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(frontend_router, tags=["frontend"])

    return app
