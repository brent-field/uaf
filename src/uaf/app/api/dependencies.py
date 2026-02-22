"""Dependency injection for FastAPI — SecureGraphDB, Session, LensRegistry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request

from uaf.core.errors import AuthenticationError
from uaf.security.auth import TokenCredentials

if TYPE_CHECKING:
    from uaf.app.lenses import LensRegistry
    from uaf.security.secure_graph_db import SecureGraphDB, Session


def get_db(request: Request) -> SecureGraphDB:
    """Get the SecureGraphDB instance from app state."""
    return request.app.state.db  # type: ignore[no-any-return]


def get_registry(request: Request) -> LensRegistry:
    """Get the LensRegistry instance from app state."""
    return request.app.state.registry  # type: ignore[no-any-return]


def get_session(
    request: Request, db: SecureGraphDB = Depends(get_db)
) -> Session:
    """Extract and validate the session from the Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = auth_header[7:]
    try:
        return db.authenticate(TokenCredentials(token=token))
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
