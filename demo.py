"""UAF Demo — starts the HTMX frontend on http://localhost:8000."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import uvicorn

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.doc_lens import DocLens
from uaf.app.lenses.flow_lens import FlowLens
from uaf.app.lenses.grid_lens import GridLens
from uaf.db.journaled_graph_db import JournaledGraphDB
from uaf.db.store import Store, StoreConfig
from uaf.security.acl import PermissionResolver
from uaf.security.audit import AuditLog
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB
from uaf.security.security_store import SecurityStore


def _load_or_create_jwt_secret(store_root: Path) -> str:
    """Load a stable JWT secret from disk, creating one if absent."""
    secret_path = store_root / "jwt_secret"
    if secret_path.exists():
        return secret_path.read_text().strip()
    store_root.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_hex(32)
    secret_path.write_text(secret)
    return secret


def main() -> None:
    """Bootstrap the database, register lenses, and start uvicorn."""
    store_dir = Path(os.environ.get("UAF_STORE_DIR", "./store"))
    store = Store.open_or_create(StoreConfig(root=store_dir))

    jwt_secret = _load_or_create_jwt_secret(store_dir)

    db = JournaledGraphDB(store)
    auth = LocalAuthProvider(jwt_secret=jwt_secret)

    # Shared resolver and audit log between SecurityStore and SecureGraphDB
    resolver = PermissionResolver()
    audit = AuditLog()

    sec_store = SecurityStore(
        path=store_dir / "security.jsonl",
        auth=auth,
        resolver=resolver,
        audit=audit,
    )
    sec_store.replay()

    sdb = SecureGraphDB(
        db, auth, on_security_event=sec_store.record,
        resolver=resolver, audit=audit,
    )

    registry = LensRegistry()
    registry.register(DocLens())
    registry.register(GridLens())
    registry.register(FlowLens())

    app = create_app(sdb, registry)

    print("UAF Demo running at http://localhost:8000")
    print(f"Persistent store: {store_dir.resolve()}")
    print("Register a new account or log in with an existing one.\n")

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
