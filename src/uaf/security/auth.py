"""Authentication — Credentials, AuthProvider protocol, LocalAuthProvider."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import argon2
import jwt

from uaf.core.errors import AuthenticationError
from uaf.core.node_id import utc_now
from uaf.security.primitives import Principal, PrincipalId, Role


@dataclass(frozen=True, slots=True)
class PasswordCredentials:
    """Authenticate with principal ID and password."""

    principal_id: PrincipalId
    password: str


@dataclass(frozen=True, slots=True)
class TokenCredentials:
    """Authenticate with a JWT token."""

    token: str


Credentials = PasswordCredentials | TokenCredentials


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol for authentication providers."""

    def authenticate(self, credentials: Credentials) -> Principal: ...

    def create_principal(
        self,
        display_name: str,
        password: str,
        *,
        roles: frozenset[Role] = frozenset(),
    ) -> Principal: ...

    def get_principal(self, principal_id: PrincipalId) -> Principal | None: ...


class LocalAuthProvider:
    """In-memory authentication with argon2 password hashing and JWT tokens."""

    def __init__(
        self,
        *,
        jwt_secret: str | None = None,
        token_lifetime_seconds: int = 3600,
    ) -> None:
        self._principals: dict[PrincipalId, Principal] = {}
        self._password_hashes: dict[PrincipalId, str] = {}
        self._jwt_secret = jwt_secret or secrets.token_hex(32)
        self._token_lifetime_seconds = token_lifetime_seconds
        self._hasher = argon2.PasswordHasher()

    def create_principal(
        self,
        display_name: str,
        password: str,
        *,
        roles: frozenset[Role] = frozenset(),
    ) -> Principal:
        """Register a new principal with a password."""
        principal = Principal(
            id=PrincipalId.generate(),
            display_name=display_name,
            roles=roles,
        )
        self._principals[principal.id] = principal
        self._password_hashes[principal.id] = self._hasher.hash(password)
        return principal

    def authenticate(self, credentials: Credentials) -> Principal:
        """Authenticate with either password or token credentials."""
        match credentials:
            case PasswordCredentials(principal_id=pid, password=password):
                return self._authenticate_password(pid, password)
            case TokenCredentials(token=token):
                return self._authenticate_token(token)

    def get_principal(self, principal_id: PrincipalId) -> Principal | None:
        """Look up a principal by ID."""
        return self._principals.get(principal_id)

    def issue_token(self, principal: Principal) -> str:
        """Generate a JWT token for an authenticated principal."""
        from datetime import timedelta

        now = utc_now()
        payload = {
            "sub": principal.id.value,
            "exp": now + timedelta(seconds=self._token_lifetime_seconds),
            "iat": now,
        }
        return jwt.encode(payload, self._jwt_secret, algorithm="HS256")

    def _authenticate_password(self, principal_id: PrincipalId, password: str) -> Principal:
        """Verify password and return principal."""
        stored_hash = self._password_hashes.get(principal_id)
        if stored_hash is None:
            msg = "Unknown principal"
            raise AuthenticationError(msg)
        try:
            self._hasher.verify(stored_hash, password)
        except argon2.exceptions.VerifyMismatchError:
            msg = "Invalid password"
            raise AuthenticationError(msg) from None

        # Rehash if needed (argon2 parameter upgrade)
        if self._hasher.check_needs_rehash(stored_hash):
            self._password_hashes[principal_id] = self._hasher.hash(password)

        principal = self._principals.get(principal_id)
        if principal is None:
            msg = "Unknown principal"
            raise AuthenticationError(msg)
        return principal

    def _authenticate_token(self, token: str) -> Principal:
        """Verify JWT token and return principal."""
        try:
            payload = jwt.decode(token, self._jwt_secret, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            msg = "Token expired"
            raise AuthenticationError(msg) from None
        except jwt.InvalidTokenError:
            msg = "Invalid token"
            raise AuthenticationError(msg) from None

        sub = payload.get("sub")
        if not isinstance(sub, str):
            msg = "Invalid token payload"
            raise AuthenticationError(msg)

        principal_id = PrincipalId(value=sub)
        principal = self._principals.get(principal_id)
        if principal is None:
            msg = "Unknown principal"
            raise AuthenticationError(msg)
        return principal
