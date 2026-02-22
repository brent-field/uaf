"""Security primitives — PrincipalId, Principal, Role, Permission, and role-permission mapping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum, unique


@dataclass(frozen=True, slots=True)
class PrincipalId:
    """Unique identifier for a principal, wrapping a string."""

    value: str

    @classmethod
    def generate(cls) -> PrincipalId:
        return cls(value=str(uuid.uuid4()))

    def __str__(self) -> str:
        return self.value


@unique
class Role(Enum):
    """Roles that can be assigned to principals on artifacts."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"
    COMMENTER = "commenter"


@unique
class Permission(Enum):
    """Granular permissions checked during authorization."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    GRANT = "grant"
    ADMIN = "admin"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset({
        Permission.READ,
        Permission.WRITE,
        Permission.DELETE,
        Permission.GRANT,
        Permission.ADMIN,
    }),
    Role.EDITOR: frozenset({Permission.READ, Permission.WRITE}),
    Role.VIEWER: frozenset({Permission.READ}),
    Role.COMMENTER: frozenset({Permission.READ}),
}


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated identity with roles and attributes."""

    id: PrincipalId
    display_name: str
    roles: frozenset[Role] = frozenset()
    attributes: tuple[tuple[str, str], ...] = ()


SYSTEM = Principal(
    id=PrincipalId(value="__system__"),
    display_name="System",
    roles=frozenset(Role),
)

ANONYMOUS = Principal(
    id=PrincipalId(value="__anonymous__"),
    display_name="Anonymous",
    roles=frozenset(),
)
