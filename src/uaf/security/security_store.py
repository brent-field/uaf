"""SecurityStore — persist and replay security events (principals, ACLs, audit)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from uaf.core.node_id import NodeId
from uaf.security.acl import ACL, ACLEntry
from uaf.security.audit import AuditAction, AuditEntry, AuditOutcome
from uaf.security.primitives import PrincipalId, Role

if TYPE_CHECKING:
    from pathlib import Path

    from uaf.security.acl import PermissionResolver
    from uaf.security.audit import AuditLog
    from uaf.security.auth import LocalAuthProvider
    from uaf.security.primitives import Principal


class SecurityStore:
    """Persist and replay security events to/from a JSONL file.

    Events include:
    - ``create_principal``: new user registration
    - ``register_artifact``: new artifact registered in resolver
    - ``register_parent``: parent-child relationship for permission inheritance
    - ``set_acl``: ACL creation or update
    - ``audit``: audit log entries

    On startup, ``replay()`` rebuilds the auth provider, permission resolver,
    and audit log from the persisted events.
    """

    def __init__(
        self,
        path: Path,
        auth: LocalAuthProvider,
        resolver: PermissionResolver,
        audit: AuditLog,
    ) -> None:
        self._path = path
        self._auth = auth
        self._resolver = resolver
        self._audit = audit
        self._file: Any = None

    def record(self, event: dict[str, Any]) -> None:
        """Write a security event to the JSONL file."""
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
        f = self._ensure_open()
        f.write(line + "\n")
        f.flush()

    def record_principal(
        self, principal: Principal, password_hash: str
    ) -> None:
        """Record a principal creation event."""
        self.record({
            "type": "create_principal",
            "principal_id": principal.id.value,
            "display_name": principal.display_name,
            "password_hash": password_hash,
            "roles": [r.value for r in principal.roles],
        })

    def replay(self) -> None:
        """Replay all security events to rebuild in-memory state."""
        if not self._path.exists():
            return

        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    event = json.loads(stripped)
                    self._apply_event(event)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

    def close(self) -> None:
        """Close the file handle."""
        if self._file is not None:
            self._file.close()
            self._file = None

    def _apply_event(self, event: dict[str, Any]) -> None:
        """Apply a single security event to rebuild state."""
        event_type = event.get("type")

        if event_type == "create_principal":
            self._replay_create_principal(event)
        elif event_type == "register_artifact":
            self._replay_register_artifact(event)
        elif event_type == "register_parent":
            self._replay_register_parent(event)
        elif event_type == "set_acl":
            self._replay_set_acl(event)
        elif event_type == "audit":
            self._replay_audit(event)

    def _replay_create_principal(self, event: dict[str, Any]) -> None:
        from uaf.security.primitives import Principal

        pid = PrincipalId(value=event["principal_id"])
        roles = frozenset(Role(r) for r in event.get("roles", []))
        principal = Principal(
            id=pid,
            display_name=event["display_name"],
            roles=roles,
        )
        self._auth._principals[pid] = principal
        self._auth._password_hashes[pid] = event["password_hash"]

    def _replay_register_artifact(self, event: dict[str, Any]) -> None:
        nid = NodeId(value=uuid.UUID(event["artifact_id"]))
        self._resolver.register_artifact(nid)

    def _replay_register_parent(self, event: dict[str, Any]) -> None:
        child = NodeId(value=uuid.UUID(event["child_id"]))
        parent = NodeId(value=uuid.UUID(event["parent_id"]))
        self._resolver.register_parent(child, parent)

    def _replay_set_acl(self, event: dict[str, Any]) -> None:
        artifact_id = NodeId(value=uuid.UUID(event["artifact_id"]))
        entries: list[ACLEntry] = []
        for e in event.get("entries", []):
            entries.append(ACLEntry(
                principal_id=PrincipalId(value=e["principal_id"]),
                role=Role(e["role"]),
                granted_at=datetime.fromisoformat(e["granted_at"]),
                granted_by=PrincipalId(value=e["granted_by"]),
            ))
        default_role_str = event.get("default_role")
        default_role = Role(default_role_str) if default_role_str else None
        acl = ACL(
            artifact_id=artifact_id,
            entries=tuple(entries),
            default_role=default_role,
            public_read=event.get("public_read", False),
        )
        self._resolver.set_acl(acl)

    def _replay_audit(self, event: dict[str, Any]) -> None:
        artifact_id_str = event.get("artifact_id")
        artifact_id = (
            NodeId(value=uuid.UUID(artifact_id_str))
            if artifact_id_str
            else None
        )
        entry = AuditEntry(
            operation_id=None,
            principal_id=PrincipalId(value=event["principal_id"]),
            timestamp=datetime.fromisoformat(event["timestamp"]),
            action=AuditAction(event["action"]),
            target_id=NodeId(value=uuid.UUID(event["target_id"])),
            artifact_id=artifact_id,
            outcome=AuditOutcome(event["outcome"]),
        )
        self._audit.record(entry)

    def _ensure_open(self) -> Any:
        if self._file is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = self._path.open("a", encoding="utf-8")
        return self._file
