"""UAF Demo — starts the HTMX frontend on http://localhost:8000."""

from __future__ import annotations

import uvicorn

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.doc_lens import DocLens
from uaf.app.lenses.flow_lens import FlowLens
from uaf.app.lenses.grid_lens import GridLens
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB


def main() -> None:
    """Bootstrap the database, register lenses, and start uvicorn."""
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)

    registry = LensRegistry()
    registry.register(DocLens())
    registry.register(GridLens())
    registry.register(FlowLens())

    app = create_app(sdb, registry)

    print("UAF Demo running at http://localhost:8000")
    print("Register a new account to get started.\n")

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
