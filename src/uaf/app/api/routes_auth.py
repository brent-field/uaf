"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from uaf.app.api.dependencies import get_db, get_session
from uaf.app.api.schemas import (
    LoginRequest,
    PrincipalResponse,
    RegisterRequest,
    TokenResponse,
)
from uaf.core.errors import AuthenticationError
from uaf.security.auth import PasswordCredentials
from uaf.security.primitives import PrincipalId
from uaf.security.secure_graph_db import SecureGraphDB, Session

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: SecureGraphDB = Depends(get_db)) -> TokenResponse:
    """Authenticate and return a session token."""
    try:
        session = db.authenticate(
            PasswordCredentials(
                principal_id=PrincipalId(value=body.principal_id),
                password=body.password,
            )
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e
    return TokenResponse(
        token=session.token,
        principal_id=session.principal.id.value,
        display_name=session.principal.display_name,
    )


@router.post("/register", response_model=TokenResponse)
def register(body: RegisterRequest, db: SecureGraphDB = Depends(get_db)) -> TokenResponse:
    """Create a new principal and return a session token."""
    from uaf.security.auth import LocalAuthProvider

    auth = db._auth
    if not isinstance(auth, LocalAuthProvider):
        raise HTTPException(status_code=501, detail="Registration not supported")

    principal = auth.create_principal(body.display_name, body.password)
    session = db.authenticate(
        PasswordCredentials(principal_id=principal.id, password=body.password)
    )
    return TokenResponse(
        token=session.token,
        principal_id=session.principal.id.value,
        display_name=session.principal.display_name,
    )


@router.get("/me", response_model=PrincipalResponse)
def me(session: Session = Depends(get_session)) -> PrincipalResponse:
    """Get the current authenticated principal."""
    return PrincipalResponse(
        principal_id=session.principal.id.value,
        display_name=session.principal.display_name,
    )
