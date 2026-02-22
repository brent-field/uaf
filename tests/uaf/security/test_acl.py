"""Tests for ACL model — ACLEntry, ACL, NodePermissionOverride, PermissionResolver."""

from __future__ import annotations

from uaf.core.node_id import NodeId, utc_now
from uaf.security.acl import ACL, ACLEntry, NodePermissionOverride, PermissionResolver
from uaf.security.primitives import (
    ANONYMOUS,
    SYSTEM,
    Permission,
    Principal,
    PrincipalId,
    Role,
)


def _pid(name: str) -> PrincipalId:
    return PrincipalId(value=name)


def _principal(name: str) -> Principal:
    return Principal(id=_pid(name), display_name=name)


def _entry(name: str, role: Role, by: str = "admin") -> ACLEntry:
    return ACLEntry(
        principal_id=_pid(name),
        role=role,
        granted_at=utc_now(),
        granted_by=_pid(by),
    )


def _setup_resolver(
    *,
    acl: ACL | None = None,
    override: NodePermissionOverride | None = None,
    children: dict[NodeId, NodeId] | None = None,
    artifact_id: NodeId | None = None,
) -> PermissionResolver:
    """Helper to build a PermissionResolver with common setup."""
    resolver = PermissionResolver()
    if artifact_id is not None:
        resolver.register_artifact(artifact_id)
    if acl is not None:
        resolver.set_acl(acl)
    if override is not None:
        resolver.set_override(override)
    if children is not None:
        for child, parent in children.items():
            resolver.register_parent(child, parent)
    return resolver


class TestACLEntryAndACL:
    def test_acl_entry_frozen(self) -> None:
        e = _entry("alice", Role.EDITOR)
        try:
            e.role = Role.VIEWER  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass

    def test_acl_frozen(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=())
        try:
            acl.public_read = True  # type: ignore[misc]
            raise AssertionError("Should be frozen")
        except AttributeError:
            pass

    def test_acl_defaults(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=())
        assert acl.default_role is None
        assert acl.public_read is False


class TestPermissionResolverExplicitGrants:
    def test_owner_can_do_everything(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("alice", Role.OWNER),))
        resolver = _setup_resolver(acl=acl, artifact_id=art_id)
        alice = _principal("alice")
        for perm in Permission:
            assert resolver.resolve(alice, art_id, perm) is True

    def test_editor_can_read_and_write(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("bob", Role.EDITOR),))
        resolver = _setup_resolver(acl=acl, artifact_id=art_id)
        bob = _principal("bob")
        assert resolver.resolve(bob, art_id, Permission.READ) is True
        assert resolver.resolve(bob, art_id, Permission.WRITE) is True
        assert resolver.resolve(bob, art_id, Permission.DELETE) is False
        assert resolver.resolve(bob, art_id, Permission.GRANT) is False

    def test_viewer_can_only_read(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("charlie", Role.VIEWER),))
        resolver = _setup_resolver(acl=acl, artifact_id=art_id)
        charlie = _principal("charlie")
        assert resolver.resolve(charlie, art_id, Permission.READ) is True
        assert resolver.resolve(charlie, art_id, Permission.WRITE) is False


class TestPermissionResolverInheritance:
    def test_child_inherits_artifact_acl(self) -> None:
        art_id = NodeId.generate()
        child_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("alice", Role.EDITOR),))
        resolver = _setup_resolver(
            acl=acl, artifact_id=art_id, children={child_id: art_id}
        )
        alice = _principal("alice")
        assert resolver.resolve(alice, child_id, Permission.READ) is True
        assert resolver.resolve(alice, child_id, Permission.WRITE) is True

    def test_grandchild_inherits_artifact_acl(self) -> None:
        art_id = NodeId.generate()
        child_id = NodeId.generate()
        grandchild_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("alice", Role.VIEWER),))
        resolver = _setup_resolver(
            acl=acl,
            artifact_id=art_id,
            children={child_id: art_id, grandchild_id: child_id},
        )
        alice = _principal("alice")
        assert resolver.resolve(alice, grandchild_id, Permission.READ) is True


class TestPermissionResolverDefaultRole:
    def test_default_role_applies_to_unlisted_principal(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(), default_role=Role.VIEWER)
        resolver = _setup_resolver(acl=acl, artifact_id=art_id)
        stranger = _principal("stranger")
        assert resolver.resolve(stranger, art_id, Permission.READ) is True
        assert resolver.resolve(stranger, art_id, Permission.WRITE) is False

    def test_explicit_entry_overrides_default_role(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(
            artifact_id=art_id,
            entries=(_entry("alice", Role.EDITOR),),
            default_role=Role.VIEWER,
        )
        resolver = _setup_resolver(acl=acl, artifact_id=art_id)
        alice = _principal("alice")
        assert resolver.resolve(alice, art_id, Permission.WRITE) is True


class TestPermissionResolverPublicRead:
    def test_public_read_allows_anonymous_read(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(), public_read=True)
        resolver = _setup_resolver(acl=acl, artifact_id=art_id)
        assert resolver.resolve(ANONYMOUS, art_id, Permission.READ) is True

    def test_public_read_does_not_allow_write(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(), public_read=True)
        resolver = _setup_resolver(acl=acl, artifact_id=art_id)
        assert resolver.resolve(ANONYMOUS, art_id, Permission.WRITE) is False


class TestPermissionResolverNodeOverride:
    def test_override_restricts_permissions(self) -> None:
        art_id = NodeId.generate()
        child_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("alice", Role.EDITOR),))
        override = NodePermissionOverride(
            node_id=child_id,
            entries=(_entry("alice", Role.VIEWER),),
        )
        resolver = _setup_resolver(
            acl=acl,
            artifact_id=art_id,
            children={child_id: art_id},
            override=override,
        )
        alice = _principal("alice")
        # Override restricts to VIEWER on this specific node
        assert resolver.resolve(alice, child_id, Permission.READ) is True
        assert resolver.resolve(alice, child_id, Permission.WRITE) is False
        # But artifact-level still grants EDITOR
        assert resolver.resolve(alice, art_id, Permission.WRITE) is True

    def test_override_expands_permissions(self) -> None:
        art_id = NodeId.generate()
        child_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("bob", Role.VIEWER),))
        override = NodePermissionOverride(
            node_id=child_id,
            entries=(_entry("bob", Role.EDITOR),),
        )
        resolver = _setup_resolver(
            acl=acl,
            artifact_id=art_id,
            children={child_id: art_id},
            override=override,
        )
        bob = _principal("bob")
        assert resolver.resolve(bob, child_id, Permission.WRITE) is True


class TestPermissionResolverSystemBypass:
    def test_system_bypasses_all_checks(self) -> None:
        art_id = NodeId.generate()
        # No ACL at all — SYSTEM still gets through
        resolver = PermissionResolver()
        resolver.register_artifact(art_id)
        for perm in Permission:
            assert resolver.resolve(SYSTEM, art_id, perm) is True


class TestPermissionResolverNoAccess:
    def test_no_acl_means_no_access(self) -> None:
        art_id = NodeId.generate()
        resolver = PermissionResolver()
        resolver.register_artifact(art_id)
        alice = _principal("alice")
        assert resolver.resolve(alice, art_id, Permission.READ) is False

    def test_no_artifact_means_no_access(self) -> None:
        """Node not in any artifact's containment tree."""
        orphan = NodeId.generate()
        resolver = PermissionResolver()
        alice = _principal("alice")
        assert resolver.resolve(alice, orphan, Permission.READ) is False


class TestPermissionResolverFindArtifact:
    def test_find_artifact_returns_root(self) -> None:
        art_id = NodeId.generate()
        child_id = NodeId.generate()
        resolver = PermissionResolver()
        resolver.register_artifact(art_id)
        resolver.register_parent(child_id, art_id)
        assert resolver.find_artifact(child_id) == art_id

    def test_find_artifact_returns_self_for_artifact(self) -> None:
        art_id = NodeId.generate()
        resolver = PermissionResolver()
        resolver.register_artifact(art_id)
        assert resolver.find_artifact(art_id) == art_id

    def test_find_artifact_returns_none_for_orphan(self) -> None:
        resolver = PermissionResolver()
        assert resolver.find_artifact(NodeId.generate()) is None


class TestPermissionResolverMutations:
    def test_set_and_remove_acl(self) -> None:
        art_id = NodeId.generate()
        acl = ACL(artifact_id=art_id, entries=(_entry("alice", Role.EDITOR),))
        resolver = PermissionResolver()
        resolver.register_artifact(art_id)
        resolver.set_acl(acl)
        assert resolver.get_acl(art_id) is acl
        resolver.remove_acl(art_id)
        assert resolver.get_acl(art_id) is None

    def test_unregister_parent(self) -> None:
        art_id = NodeId.generate()
        child_id = NodeId.generate()
        resolver = PermissionResolver()
        resolver.register_artifact(art_id)
        resolver.register_parent(child_id, art_id)
        assert resolver.find_artifact(child_id) == art_id
        resolver.unregister_parent(child_id)
        assert resolver.find_artifact(child_id) is None
