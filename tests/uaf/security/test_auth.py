"""Tests for authentication — LocalAuthProvider, password + token auth."""

from __future__ import annotations

from datetime import timedelta

import jwt
import pytest

from uaf.core.errors import AuthenticationError
from uaf.core.node_id import utc_now
from uaf.security.auth import (
    LocalAuthProvider,
    PasswordCredentials,
    TokenCredentials,
)
from uaf.security.primitives import PrincipalId, Role


class TestCreatePrincipal:
    def test_create_returns_principal(self) -> None:
        auth = LocalAuthProvider()
        p = auth.create_principal("Alice", "secret123")
        assert p.display_name == "Alice"

    def test_create_with_roles(self) -> None:
        auth = LocalAuthProvider()
        p = auth.create_principal("Alice", "secret123", roles=frozenset({Role.OWNER}))
        assert Role.OWNER in p.roles

    def test_create_generates_unique_id(self) -> None:
        auth = LocalAuthProvider()
        p1 = auth.create_principal("Alice", "pass1")
        p2 = auth.create_principal("Bob", "pass2")
        assert p1.id != p2.id


class TestGetPrincipal:
    def test_get_existing(self) -> None:
        auth = LocalAuthProvider()
        p = auth.create_principal("Alice", "secret123")
        assert auth.get_principal(p.id) is p

    def test_get_unknown(self) -> None:
        auth = LocalAuthProvider()
        assert auth.get_principal(PrincipalId(value="nonexistent")) is None


class TestPasswordAuth:
    def test_correct_password(self) -> None:
        auth = LocalAuthProvider()
        p = auth.create_principal("Alice", "secret123")
        result = auth.authenticate(PasswordCredentials(principal_id=p.id, password="secret123"))
        assert result.id == p.id

    def test_wrong_password(self) -> None:
        auth = LocalAuthProvider()
        p = auth.create_principal("Alice", "secret123")
        with pytest.raises(AuthenticationError, match="Invalid password"):
            auth.authenticate(PasswordCredentials(principal_id=p.id, password="wrong"))

    def test_unknown_principal(self) -> None:
        auth = LocalAuthProvider()
        with pytest.raises(AuthenticationError, match="Unknown principal"):
            auth.authenticate(
                PasswordCredentials(principal_id=PrincipalId(value="nobody"), password="pass")
            )


class TestTokenAuth:
    def test_valid_token(self) -> None:
        auth = LocalAuthProvider()
        p = auth.create_principal("Alice", "secret123")
        token = auth.issue_token(p)
        result = auth.authenticate(TokenCredentials(token=token))
        assert result.id == p.id

    def test_expired_token(self) -> None:
        auth = LocalAuthProvider(token_lifetime_seconds=0)
        p = auth.create_principal("Alice", "secret123")
        # Manually create an already-expired token
        now = utc_now()
        payload = {
            "sub": p.id.value,
            "exp": now - timedelta(seconds=10),
            "iat": now - timedelta(seconds=20),
        }
        token = jwt.encode(payload, auth._jwt_secret, algorithm="HS256")
        with pytest.raises(AuthenticationError, match="Token expired"):
            auth.authenticate(TokenCredentials(token=token))

    def test_invalid_token(self) -> None:
        auth = LocalAuthProvider()
        with pytest.raises(AuthenticationError, match="Invalid token"):
            auth.authenticate(TokenCredentials(token="garbage.token.here"))

    def test_wrong_secret(self) -> None:
        auth = LocalAuthProvider(jwt_secret="secret-a")
        p = auth.create_principal("Alice", "secret123")
        token = jwt.encode(
            {"sub": p.id.value, "exp": utc_now() + timedelta(hours=1)},
            "secret-b",
            algorithm="HS256",
        )
        with pytest.raises(AuthenticationError, match="Invalid token"):
            auth.authenticate(TokenCredentials(token=token))

    def test_token_for_deleted_principal(self) -> None:
        auth = LocalAuthProvider()
        p = auth.create_principal("Alice", "secret123")
        token = auth.issue_token(p)
        # Simulate principal removal
        del auth._principals[p.id]
        with pytest.raises(AuthenticationError, match="Unknown principal"):
            auth.authenticate(TokenCredentials(token=token))


class TestAuthProviderProtocol:
    def test_local_auth_is_auth_provider(self) -> None:
        from uaf.security.auth import AuthProvider

        auth = LocalAuthProvider()
        assert isinstance(auth, AuthProvider)
