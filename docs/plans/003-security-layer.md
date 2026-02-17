# UAF Security Layer — Implementation Plan

**Version:** 0.1 (First Draft)
**Date:** 2026-02-17
**Scope:** Security layer (`src/uaf/security/`) — authentication, authorization, encryption, audit
**Depends on:** Database layer plan (`002-database-layer.md`, Phases 0-12)

---

## 1. Context

The security layer sits **between** the database layer and the application layer in
the UAF architecture:

```
┌─────────────────────────────────┐
│        Application Layer        │
│   API endpoints, Lens UIs       │
│         (src/uaf/app/)          │
├─────────────────────────────────┤
│         Security Layer          │  ◄── THIS PLAN
│   Auth, encryption, ACL         │
│       (src/uaf/security/)       │
├─────────────────────────────────┤
│         Database Layer          │
│   Storage, queries, CRDT sync   │
│          (src/uaf/db/)          │
└─────────────────────────────────┘
```

**Design constraint:** The application layer NEVER talks directly to `GraphDB`. All
access goes through a `SecureGraphDB` wrapper that enforces authentication, authorization,
and audit logging. This is the **policy enforcement point**.

**What the database layer already provides:**
- `owner: str | None` on `NodeMetadata` — informational ownership
- `OWNED_BY` edge type — rich ownership relationships
- Append-only operation log — natural audit trail (who/what/when)
- Content addressing — integrity verification (tamper detection)
- EAVT indexes — fast permission lookups ("all nodes owned by X")

**What the database layer is missing (and this plan adds):**
- Principal identity (who is making this request?)
- Permission model (can this principal do this action on this node?)
- Encryption (can this principal read this data?)
- Audit queries (what did this principal do?)

**Requirement: Multi-user concurrent editing.** Multiple users MUST be able to edit the
same artifact simultaneously. The security layer supports this by design — ACLs grant
per-artifact roles to multiple principals, and the audit log tracks interleaved operations
from different users. The security layer is stateless with respect to editing sessions:
each operation is independently authenticated and authorized. This means concurrent
operations from different users don't conflict at the security level — conflicts are
handled by the CRDT sync layer in the database (see `002-database-layer.md` Appendix B).

**V1 security scope:** Authentication + authorization + audit. No encryption in V1 —
encryption requires key management infrastructure that's premature for a demo. But
the interfaces are designed so encryption slots in without rearchitecting.

**Software:** 100% FOSS. `PyJWT` (MIT) for tokens, `argon2-cffi` (MIT) for password
hashing, `cryptography` (Apache 2.0 / BSD) for future encryption. All stdlib otherwise.

---

## 2. Architecture

### The Security Sandwich

Every request flows through the security layer:

```
  Lens / API / MCP Tool
         │
         ▼
  ┌──────────────┐
  │ Authenticate │  "Who are you?" → Principal
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │  Authorize   │  "Can you do X to node Y?" → Allow / Deny
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │   GraphDB    │  Execute the operation
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │    Audit     │  Log who did what, when
  └──────────────┘
```

The `SecureGraphDB` facade composes these steps. It wraps `GraphDB` and exposes the
same query/mutation interface, but every method requires a `Principal` and enforces
permissions before delegating to the underlying `GraphDB`.

### Principal Model

A **Principal** represents an authenticated identity:

```
Principal (frozen dataclass):
    id: PrincipalId           # unique identifier
    display_name: str         # human-readable name
    roles: frozenset[Role]    # assigned roles
    attributes: tuple[tuple[str, str], ...]  # key-value pairs for ABAC
```

Principals are NOT nodes in the graph — they exist outside the data model. This
separation is deliberate: you don't want a permission check to require a graph query,
which itself requires a permission check (infinite recursion).

**PrincipalId** is a frozen dataclass wrapping a string (like `NodeId` wraps UUID).
This gives type safety — you can't accidentally pass a `NodeId` where a `PrincipalId`
is expected.

**Special principals:**
- `SYSTEM` — bypasses all permission checks (used by internal operations, migrations)
- `ANONYMOUS` — unauthenticated; gets only public-read permissions

### Authentication (V1)

V1 uses **local identity** — no external IdP, no DID resolution, no OAuth. Just enough
to prove "this request comes from principal X."

```
AuthProvider (Protocol):
    def authenticate(self, credentials: Credentials) -> Principal | None
    def create_principal(self, display_name: str, ...) -> Principal
    def get_principal(self, principal_id: PrincipalId) -> Principal | None
```

**V1 implementation:** `LocalAuthProvider` — stores principals in a dict, passwords
hashed with argon2. Issues JWT tokens for session management. Simple, testable,
replaceable.

**Future implementations:**
- `DIDAuthProvider` — resolves `did:web` / `did:key` identifiers
- `OAuthAuthProvider` — delegates to external IdP (Keycloak, Auth0)
- `FederatedAuthProvider` — multi-tenant with Gaia-X compliance

The `AuthProvider` protocol means swapping auth backends doesn't touch any other code.

### Authorization Model

We use **role-based access control (RBAC)** with **node-level granularity** and
**inheritance** down the containment tree.

#### Roles

```
Role (enum):
    OWNER       — full control (read, write, delete, grant, encrypt)
    EDITOR      — read + write (create children, update, reorder)
    VIEWER      — read only (query, traverse)
    COMMENTER   — read + annotate (future: comment nodes)
```

#### Permissions

```
Permission (enum):
    READ        — view node content and metadata
    WRITE       — create, update, move, reorder nodes
    DELETE      — delete nodes and edges
    GRANT       — assign permissions to other principals
    ADMIN       — manage artifact settings, encryption keys
```

#### Role → Permission mapping

| Role | READ | WRITE | DELETE | GRANT | ADMIN |
|------|------|-------|--------|-------|-------|
| OWNER | Y | Y | Y | Y | Y |
| EDITOR | Y | Y | N | N | N |
| VIEWER | Y | N | N | N | N |
| COMMENTER | Y | N* | N | N | N |

*Commenters can create comment nodes (future) but not modify content nodes.

#### Access Control List (ACL)

Each artifact has an **ACL** — a mapping from `PrincipalId` to `Role`:

```
ACL (frozen dataclass):
    artifact_id: NodeId
    entries: tuple[ACLEntry, ...]
    default_role: Role | None      # for authenticated users not in the list
    public_read: bool              # anonymous access

ACLEntry (frozen dataclass):
    principal_id: PrincipalId
    role: Role
    granted_at: datetime
    granted_by: PrincipalId
```

#### Permission Resolution

Permissions are resolved **top-down** through the containment tree:

```
1. Find the artifact (root) that contains this node
   (walk CONTAINS edges upward)
2. Look up the ACL for that artifact
3. Find the principal's role in the ACL
4. Check if the role grants the requested permission
```

**Inheritance rule:** All nodes within an artifact inherit the artifact's ACL by
default. A node can optionally have a **node-level override** that restricts or
expands permissions for specific principals.

```
NodePermissionOverride (frozen dataclass):
    node_id: NodeId
    entries: tuple[ACLEntry, ...]   # overrides artifact-level role
```

**Why artifact-level, not node-level by default?** Because most users think in terms of
"who can access this document/spreadsheet/presentation" — not "who can access paragraph 3."
Node-level overrides handle the exception cases (sensitive sections, restricted formulas).

#### Transclusion and Permissions

This resolves Appendix C3 from the database plan:

When node X is transcluded (via `REFERENCES` edge) from artifact A into artifact B:
- **Viewing X via artifact A:** A's ACL applies
- **Viewing X via artifact B:** B's ACL applies, BUT the viewer must ALSO have READ
  on X in its home artifact (A)

In other words: **transclusion does not grant access.** You need permission in BOTH
the source and destination artifacts. This is the safest default — it prevents
information leaks from private artifacts.

**Implementation:** `SecureGraphDB.get_node()` checks:
1. Does the principal have READ on the artifact being viewed?
2. If the node is transcluded (has a `REFERENCES` edge from another artifact),
   does the principal have READ on the source artifact too?
3. If either check fails, the node is filtered from results.

### Audit Trail

The operation log already records what happened and when. The security layer adds
**who**:

```
AuditEntry (frozen dataclass):
    operation_id: OperationId     # links to the operation log entry
    principal_id: PrincipalId     # who performed the action
    timestamp: datetime           # when (redundant with op, but useful for queries)
    action: AuditAction           # what type of action
    target_id: NodeId | EdgeId    # what was affected
    artifact_id: NodeId           # which artifact context
    outcome: AuditOutcome         # ALLOWED or DENIED

AuditAction (enum):
    CREATE_NODE, UPDATE_NODE, DELETE_NODE,
    CREATE_EDGE, DELETE_EDGE, MOVE_NODE,
    GRANT_PERMISSION, REVOKE_PERMISSION,
    READ_NODE, QUERY  # optional: log reads for compliance

AuditOutcome (enum):
    ALLOWED, DENIED
```

**AuditLog** — append-only log of all security-relevant actions:

| Method | Description |
|--------|-------------|
| `record(entry)` | Append an audit entry |
| `for_principal(id)` | All actions by a principal |
| `for_node(id)` | All actions on a node |
| `for_artifact(id)` | All actions within an artifact |
| `denied()` | All denied actions (security monitoring) |
| `since(timestamp)` | All actions after a time |

**Design:** The audit log is separate from the operation log. The operation log is the
source of truth for data; the audit log is the source of truth for access. They cross-
reference via `OperationId` for allowed mutations.

**GDPR consideration:** Audit logs contain personal data (principal IDs + actions).
They must be subject to retention policies and right-to-erasure (pseudonymization of
principal IDs after a retention period).

### Encryption (Future — Interface Defined Now)

V1 does NOT implement encryption, but the interfaces are defined so it slots in later
without rearchitecting.

```
EncryptionProvider (Protocol):
    def encrypt_node(self, node: NodeData, key: EncryptionKey) -> EncryptedNode
    def decrypt_node(self, encrypted: EncryptedNode, key: EncryptionKey) -> NodeData
    def encrypt_blob(self, data: bytes, key: EncryptionKey) -> bytes
    def decrypt_blob(self, data: bytes, key: EncryptionKey) -> bytes
```

**Object-level encryption:** Each artifact gets its own encryption key. Nodes within
the artifact are encrypted with that key. Sharing the artifact means sharing the key
(via key wrapping with the recipient's public key).

**Zero-knowledge hosting:** The server stores encrypted nodes. It can execute structural
queries (EAVT indexes work on encrypted attribute names if we use deterministic
encryption for index fields), but cannot read content. This is the "Sovereign Vault"
from the business model.

**Crypto-shredding for GDPR:** To "delete" a user's data, destroy their encryption key.
The encrypted data becomes unrecoverable without brute force. This solves the
"append-only log + right to erasure" conflict.

**Key management options (future evaluation):**

| Approach | Pros | Cons |
|----------|------|------|
| Per-artifact symmetric key, wrapped with user public keys | Simple, standard | Key distribution for sharing |
| Envelope encryption (KMS-style) | Industry standard, integrates with cloud KMS | Requires external service |
| DID-based key agreement (X25519) | Decentralized, aligns with DID auth | Complex, less mature tooling |

---

## 3. Database Layer Integration Points

The security layer needs specific hooks into the database layer. These are the
**alignment points** between the two plans:

### 3.1 Operation Metadata Extension

Operations in the database layer currently have `parent_ops` and `timestamp`. The
security layer needs to add `principal_id` to every operation:

```
# Current (db layer Phase 5):
CreateNode: node, parent_ops, timestamp

# With security:
CreateNode: node, parent_ops, timestamp, principal_id: PrincipalId | None
```

**Approach:** Add an optional `principal_id: PrincipalId | None` field to the shared
operation base fields. `None` means the operation was created before the security layer
existed (migration compatibility) or by `SYSTEM`. The database layer stores it; the
security layer populates it.

**Impact on db plan:** Minor change to Phase 5 (operations.py) — add one optional field.
Serialization (Phase 4) handles it automatically. Content hash now includes principal_id
when present.

### 3.2 ACL Storage

ACLs are stored as **nodes and edges in the graph itself**:

```
ArtifactACL (node type):
    meta: NodeMetadata
    default_role: Role | None
    public_read: bool

PermissionGrant (edge type):
    source: ArtifactACL node
    target: (conceptually a Principal, but stored as properties)
    edge_type: GRANTS_ROLE
    properties: (("principal_id", "..."), ("role", "EDITOR"), ...)
```

**Why store ACLs in the graph?** Because they benefit from the same infrastructure:
content addressing (tamper detection), operation log (permission change history), CRDT
sync (permission changes propagate across replicas).

**Performance concern:** Permission checks happen on every operation. We can't do a
graph traversal on every `get_node()`. Solution: **materialized permission cache** — a
`dict[NodeId, dict[PrincipalId, Role]]` that the security layer maintains, updated
whenever ACL nodes/edges change. Same pattern as `MaterializedState` in the db layer.

### 3.3 EAVT Index Usage

The security layer leverages existing EAVT indexes:

| Security Query | EAVT Index | Pattern |
|---------------|-----------|---------|
| "All artifacts owned by user X" | AVET | `attr="owner", value="user-X"` |
| "All nodes in artifact Y" | Walk CONTAINS from Y | Containment tree |
| "All permission grants for artifact Y" | VAET | `value="artifact-Y-acl-node"` |
| "All audit entries for user X" | Audit log index | Separate from EAVT |

### 3.4 Operation Log as Audit Source

The security layer's audit log cross-references the database layer's operation log:

```
Operation Log (db layer):    op_id, operation, parent_ops, timestamp
Audit Log (security layer):  op_id, principal_id, action, target, outcome
```

For every `GraphDB.apply()` call, `SecureGraphDB`:
1. Resolves the principal from the session/token
2. Checks authorization (ACL lookup)
3. If denied: logs to audit log with `DENIED`, raises `PermissionDeniedError`
4. If allowed: delegates to `GraphDB.apply()`, logs to audit log with `ALLOWED`

---

## 4. Implementation Phases

Each phase produces a green `make check`. Phases S1-S4 can begin after db layer
Phase 12 is complete.

### Phase S1: Security Primitives — `src/uaf/security/primitives.py`

| Type | Description |
|------|-------------|
| `PrincipalId` | Frozen dataclass wrapping a string, with `generate()` classmethod |
| `Principal` | Frozen dataclass: id, display_name, roles, attributes |
| `Role` | Enum: OWNER, EDITOR, VIEWER, COMMENTER |
| `Permission` | Enum: READ, WRITE, DELETE, GRANT, ADMIN |
| `ROLE_PERMISSIONS` | Frozen dict mapping Role → frozenset[Permission] |
| `SYSTEM` | Singleton Principal that bypasses all checks |
| `ANONYMOUS` | Singleton Principal with no roles |

**Tests:** `tests/uaf/security/test_primitives.py` (~10 tests: role-permission mapping,
principal immutability, SYSTEM/ANONYMOUS behavior)

---

### Phase S2: ACL Model — `src/uaf/security/acl.py`

| Type | Description |
|------|-------------|
| `ACLEntry` | Frozen dataclass: principal_id, role, granted_at, granted_by |
| `ACL` | Frozen dataclass: artifact_id, entries, default_role, public_read |
| `NodePermissionOverride` | Frozen dataclass: node_id, entries |
| `PermissionResolver` | Resolves effective permissions for a principal on a node |

**PermissionResolver** logic:

```python
def resolve(principal: Principal, node_id: NodeId, action: Permission) -> bool:
    # 1. SYSTEM bypasses all checks
    if principal is SYSTEM: return True

    # 2. Check node-level override first
    if override := self._overrides.get(node_id):
        if entry := find_entry(override, principal.id):
            return action in ROLE_PERMISSIONS[entry.role]

    # 3. Walk up to artifact root, get ACL
    artifact_id = self._find_artifact(node_id)
    acl = self._acls.get(artifact_id)
    if acl is None: return False  # no ACL = no access

    # 4. Check principal's explicit role
    if entry := find_entry(acl, principal.id):
        return action in ROLE_PERMISSIONS[entry.role]

    # 5. Check default role
    if acl.default_role is not None:
        return action in ROLE_PERMISSIONS[acl.default_role]

    # 6. Check public read
    if acl.public_read and action == Permission.READ:
        return True

    return False
```

**Tests:** `tests/uaf/security/test_acl.py` (~18 tests: explicit grants, default role,
public read, node overrides, SYSTEM bypass, ANONYMOUS access, inheritance from artifact)

---

### Phase S3: Audit Log — `src/uaf/security/audit.py`

| Type | Description |
|------|-------------|
| `AuditAction` | Enum of all auditable actions |
| `AuditOutcome` | Enum: ALLOWED, DENIED |
| `AuditEntry` | Frozen dataclass with all audit fields |
| `AuditLog` | Append-only log with query methods |

**AuditLog** methods:

| Method | Returns |
|--------|---------|
| `record(entry)` | None (append) |
| `for_principal(id, since=None)` | `list[AuditEntry]` |
| `for_node(id, since=None)` | `list[AuditEntry]` |
| `for_artifact(id, since=None)` | `list[AuditEntry]` |
| `denied(since=None)` | `list[AuditEntry]` |
| `count()` | `int` |

**V1 storage:** In-memory list + dict indexes (by principal, by node, by artifact).
Same pattern as OperationLog.

**Tests:** `tests/uaf/security/test_audit.py` (~12 tests: recording, querying by
principal/node/artifact, denied-only filter, time range filtering)

---

### Phase S4: Authentication — `src/uaf/security/auth.py`

| Type | Description |
|------|-------------|
| `Credentials` | Union type: `PasswordCredentials \| TokenCredentials` |
| `PasswordCredentials` | Frozen dataclass: principal_id, password |
| `TokenCredentials` | Frozen dataclass: jwt_token |
| `AuthProvider` | Protocol: authenticate, create_principal, get_principal |
| `LocalAuthProvider` | In-memory implementation with argon2 password hashing + JWT |

**LocalAuthProvider** internals:
- `_principals: dict[PrincipalId, Principal]` — principal store
- `_password_hashes: dict[PrincipalId, str]` — argon2 hashes
- `_jwt_secret: str` — signing key (generated at init)
- `authenticate(PasswordCredentials) -> Principal | None` — verify password, return principal
- `authenticate(TokenCredentials) -> Principal | None` — verify JWT, return principal
- `issue_token(principal) -> str` — generate JWT with expiry
- `create_principal(display_name, password) -> Principal` — register new principal

**Tests:** `tests/uaf/security/test_auth.py` (~14 tests: create principal, authenticate
with password, authenticate with token, expired token rejection, wrong password rejection,
unknown principal)

---

### Phase S5: SecureGraphDB Facade — `src/uaf/security/secure_graph_db.py`

The main entry point for the application layer. Wraps `GraphDB` with security enforcement.

```python
class SecureGraphDB:
    def __init__(self, db: GraphDB, auth: AuthProvider) -> None:
        self._db = db
        self._auth = auth
        self._resolver = PermissionResolver()
        self._audit = AuditLog()

    # --- Authentication ---
    def authenticate(self, credentials: Credentials) -> Session: ...
    def get_principal(self, session: Session) -> Principal: ...

    # --- Mutations (require session + permission) ---
    def create_node(self, session: Session, node: NodeData) -> NodeId: ...
    def update_node(self, session: Session, node: NodeData) -> OperationId: ...
    def delete_node(self, session: Session, node_id: NodeId) -> OperationId: ...
    def create_edge(self, session: Session, edge: Edge) -> OperationId: ...

    # --- Queries (filtered by permissions) ---
    def get_node(self, session: Session, node_id: NodeId) -> NodeData | None: ...
    def get_children(self, session: Session, parent_id: NodeId) -> list[NodeData]: ...
    def find_by_type(self, session: Session, node_type: NodeType) -> list[NodeData]: ...

    # --- Permission management ---
    def grant_role(self, session: Session, artifact_id: NodeId,
                   target_principal: PrincipalId, role: Role) -> None: ...
    def revoke_role(self, session: Session, artifact_id: NodeId,
                    target_principal: PrincipalId) -> None: ...
    def get_acl(self, session: Session, artifact_id: NodeId) -> ACL: ...

    # --- Audit ---
    def get_audit_log(self, session: Session, ...) -> list[AuditEntry]: ...
```

**Session** — frozen dataclass containing `principal: Principal` and `token: str`.
Created by `authenticate()`, required by all other methods.

**Convenience methods:**
- `system_session() -> Session` — returns a session for the `SYSTEM` principal. Used
  by internal operations (format import/export, tests, migrations) that bypass all
  permission checks. See `004-application-layer.md` §9.

**Query filtering:** Methods like `find_by_type()` delegate to `GraphDB`, then filter
results to only include nodes the principal has READ access to. This means the db layer
does the heavy lifting (index scans), and the security layer does post-filtering.

**Tests:** `tests/uaf/security/test_secure_graph_db.py` (~20 tests: full CRUD with
permissions, denied access, query filtering, permission grant/revoke, audit log population,
SYSTEM bypass, transclusion permission checks)

---

### Phase S6: Security Exports + Integration Tests

**`src/uaf/security/__init__.py`** — wire up `__all__`

**`tests/uaf/security/test_integration.py`** — scenario tests:
1. **Multi-user document:** Owner creates artifact, grants EDITOR to user 2, user 2
   adds content, user 3 (no access) gets denied
2. **Permission change:** Owner revokes EDITOR from user 2, user 2 can still read but
   not write
3. **Transclusion across artifacts:** User 1 owns artifact A, user 2 owns artifact B.
   Node X is in A, transcluded into B. User 2 can see X via B only if they also have
   READ on A
4. **Audit trail:** All operations from scenarios 1-3 appear in audit log with correct
   principals, actions, and outcomes
5. **SYSTEM principal:** Internal operations bypass all permission checks
6. **Public artifact:** Set `public_read=True`, verify ANONYMOUS can read but not write

---

## 5. File Summary

### Source Files (6)

| File | Purpose |
|------|---------|
| `src/uaf/security/primitives.py` | PrincipalId, Principal, Role, Permission |
| `src/uaf/security/acl.py` | ACL, ACLEntry, NodePermissionOverride, PermissionResolver |
| `src/uaf/security/audit.py` | AuditEntry, AuditAction, AuditOutcome, AuditLog |
| `src/uaf/security/auth.py` | Credentials, AuthProvider, LocalAuthProvider |
| `src/uaf/security/secure_graph_db.py` | SecureGraphDB facade |
| `src/uaf/security/__init__.py` | Public exports |

### Test Files (6)

```
tests/uaf/security/test_primitives.py        (~10 tests)
tests/uaf/security/test_acl.py               (~18 tests)
tests/uaf/security/test_audit.py             (~12 tests)
tests/uaf/security/test_auth.py              (~14 tests)
tests/uaf/security/test_secure_graph_db.py   (~20 tests)
tests/uaf/security/test_integration.py       (~6 scenario tests)
```

**Total: ~80 tests**

---

## 6. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| ACL at artifact level (not node level) by default | Matches user mental model ("who can access this document"), simpler to manage. Node-level overrides for exceptions |
| RBAC over ABAC | Simpler, well-understood. Roles map to permissions via a frozen dict. ABAC attributes on Principal enable future extension |
| Principals NOT stored as graph nodes | Avoids circular dependency: permission checks querying graph → graph queries requiring permission checks |
| ACLs stored as graph nodes/edges | Benefits from operation log (change history), content addressing (tamper detection), CRDT sync (replication) |
| Separate audit log from operation log | Different concerns: operation log = data truth, audit log = access truth. Cross-referenced by OperationId |
| Post-filter query results | Security layer filters after db query, not before. Simpler, no index coupling. May return fewer results than `limit` — acceptable for V1 |
| No encryption in V1 | Key management is premature. Interfaces defined so it slots in later. Crypto-shredding planned for GDPR |
| argon2 for password hashing | Industry standard, memory-hard (resistant to GPU attacks). `argon2-cffi` is MIT, well-maintained |
| JWT for session tokens | Stateless, standard, verifiable. Short-lived (1 hour default). No need for server-side session store |

---

## 7. Dependencies

```toml
# pyproject.toml additions for security layer
[project]
dependencies = [
    "sortedcontainers>=2.4",   # (from db layer)
    "PyJWT>=2.8",              # JWT token creation and verification
    "argon2-cffi>=23.1",       # Password hashing
]

# Future (not V1):
# "cryptography>=42.0"        # Object-level encryption
```

All dependencies are FOSS (MIT / Apache 2.0 / BSD).

---

## 8. Verification

### Per-phase gate
```bash
make check   # ruff check + mypy strict + pytest — must pass after every phase
```

### Testing strategy

**Level 1 — Unit tests (Phases S1-S4):** Permission resolution, role mapping, ACL
logic, password hashing, JWT verification. No GraphDB dependency.

**Level 2 — Integration tests (Phase S5-S6):** SecureGraphDB wrapping real GraphDB.
Multi-user scenarios, permission enforcement, audit trail verification.

**Level 3 — Negative tests (throughout):** Every permission check has a corresponding
"denied" test. Expired tokens rejected. Wrong passwords rejected. Unauthorized mutation
attempts logged to audit trail.

### After all phases
```bash
make check   # ~80 security tests + ~163 db tests = ~243 total
```

---

## 9. Alignment with Database Layer Plan

| DB Layer Feature | Security Layer Usage |
|-----------------|---------------------|
| `NodeMetadata.owner` | Informational; security layer uses ACLs for enforcement |
| `OWNED_BY` edge type | Rich ownership modeling; security layer reads these for delegation |
| Operation log (append-only) | Audit cross-reference; tamper detection via content hashing |
| EAVT indexes (AVET) | Fast lookup of "all nodes owned by X" |
| `GraphDB.apply()` | `SecureGraphDB` wraps this with auth + permission + audit |
| Content addressing (SHA-256) | Tamper detection for audit trail integrity |
| `UAFError` hierarchy | Security adds `PermissionDeniedError`, `AuthenticationError` |
| `BlobStore` | Future: blob encryption via `EncryptionProvider` |
| Graph constraints | Security layer adds additional constraint: "principal has permission" |
| Schema evolution (`RawNode`) | ACL nodes use schema versioning for backward compat |

### Changes needed in the database layer

These are **minor, additive** changes — no rearchitecting:

1. **`principal_id` on operations** (Phase 5) — Add optional `PrincipalId | None` field
   to operation base fields. `None` for backward compatibility.

2. **New error types** (Phase 1, errors.py) — Add `PermissionDeniedError` and
   `AuthenticationError` to the `UAFError` hierarchy.

3. **New edge type** (Phase 3) — Add `GRANTS_ROLE` to `EdgeType` enum for ACL edges.

4. **New node type** (Phase 2) — Add `ArtifactACL` to `NodeType` enum and node types.

---

## 10. GDPR Compliance Roadmap

The EU sovereignty requirement from the vision document maps to specific security features:

| GDPR Requirement | Implementation | Phase |
|-----------------|----------------|-------|
| **Right to access** | `audit_log.for_principal(id)` returns all data about a user | S3 |
| **Right to erasure** | Crypto-shredding: destroy user's encryption key → all their data becomes unrecoverable | Future (encryption) |
| **Right to portability** | Export pipeline (db layer Phase 13) exports all user-owned artifacts | Already planned |
| **Data minimization** | Node-level access control ensures users only see what they need | S2 |
| **Audit trail** | Full audit log of all access and mutations | S3 |
| **Consent tracking** | Future: consent node type linked to data nodes | Future |
| **Data residency** | Future: federation with region-aware storage backends | Future |
| **Breach notification** | Audit log anomaly detection (many denied accesses, unusual patterns) | Future |

**Crypto-shredding solves the hardest GDPR problem:** The append-only operation log
cannot be modified (by design — integrity depends on immutability). But GDPR requires
the right to erasure. Solution: encrypt all data with per-user keys. To "erase" a user,
destroy their key. The encrypted data remains in the log but is unrecoverable. This
satisfies GDPR Article 17 while preserving the append-only invariant.

---

## Appendix: Open Questions

### Authentication Protocol for MCP

When an AI agent connects via MCP, how does it authenticate? Options:
- **Bearer token in MCP session** — agent receives a token from the user's session
- **Service account** — dedicated principal for AI agents with scoped permissions
- **Delegated auth** — agent acts on behalf of a user (OAuth2 "on-behalf-of" flow)

Current leaning: Service account per AI agent, with explicit permission grants from
artifact owners. The agent gets its own `Principal` with `Role.EDITOR` on specific
artifacts.

### Rate Limiting / Abuse Prevention

V1 has no rate limiting. A compromised session could flood the operation log. Future:
per-principal rate limits on mutations, anomaly detection in audit log.

### Key Rotation

When encryption is added, how do we rotate artifact encryption keys? The new key must
re-encrypt all nodes in the artifact. This is a batch operation that needs to be
atomic. Possible approach: store multiple key versions, decrypt with old key + re-encrypt
with new key lazily on read.

### Multi-Tenancy

For hosted deployments, multiple organizations share infrastructure. Tenant isolation
must prevent cross-tenant data access even in the EAVT indexes. Options:
- **Separate GraphDB per tenant** — simple, strong isolation, no shared queries
- **Tenant ID prefix on all EAVT keys** — shared infrastructure, weaker isolation
- **Row-level security in persistence layer** — PostgreSQL RLS (future)

Current leaning: Separate GraphDB per tenant for V1 (simplest). Shared infrastructure
when persistence layer supports it.
