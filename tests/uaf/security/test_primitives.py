"""Tests for security primitives — PrincipalId, Principal, Role, Permission."""

from __future__ import annotations

from uaf.security.primitives import (
    ANONYMOUS,
    ROLE_PERMISSIONS,
    SYSTEM,
    Permission,
    Principal,
    PrincipalId,
    Role,
)


class TestPrincipalId:
    def test_generate_unique(self) -> None:
        a = PrincipalId.generate()
        b = PrincipalId.generate()
        assert a != b

    def test_str(self) -> None:
        pid = PrincipalId(value="alice")
        assert str(pid) == "alice"

    def test_frozen(self) -> None:
        pid = PrincipalId(value="alice")
        try:
            pid.value = "bob"  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass


class TestRolePermissionMapping:
    def test_owner_has_all_permissions(self) -> None:
        assert ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)

    def test_editor_has_read_write(self) -> None:
        assert ROLE_PERMISSIONS[Role.EDITOR] == frozenset({Permission.READ, Permission.WRITE})

    def test_viewer_has_read_only(self) -> None:
        assert ROLE_PERMISSIONS[Role.VIEWER] == frozenset({Permission.READ})

    def test_commenter_has_read_only(self) -> None:
        assert ROLE_PERMISSIONS[Role.COMMENTER] == frozenset({Permission.READ})

    def test_every_role_has_mapping(self) -> None:
        for role in Role:
            assert role in ROLE_PERMISSIONS


class TestPrincipal:
    def test_create_with_defaults(self) -> None:
        p = Principal(id=PrincipalId(value="u1"), display_name="Alice")
        assert p.roles == frozenset()
        assert p.attributes == ()

    def test_frozen(self) -> None:
        p = Principal(id=PrincipalId(value="u1"), display_name="Alice")
        try:
            p.display_name = "Bob"  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass

    def test_with_roles(self) -> None:
        p = Principal(
            id=PrincipalId(value="u1"),
            display_name="Alice",
            roles=frozenset({Role.OWNER}),
        )
        assert Role.OWNER in p.roles


class TestSystemPrincipal:
    def test_system_id(self) -> None:
        assert SYSTEM.id.value == "__system__"

    def test_system_has_all_roles(self) -> None:
        assert SYSTEM.roles == frozenset(Role)


class TestAnonymousPrincipal:
    def test_anonymous_id(self) -> None:
        assert ANONYMOUS.id.value == "__anonymous__"

    def test_anonymous_has_no_roles(self) -> None:
        assert ANONYMOUS.roles == frozenset()
