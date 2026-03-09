"""UAF Demo — starts the HTMX frontend on http://localhost:8000."""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from uaf.app.api import create_app
from uaf.app.lenses import LensRegistry
from uaf.app.lenses.doc_lens import DocLens
from uaf.app.lenses.flow_lens import FlowLens
from uaf.app.lenses.grid_lens import GridLens
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB

DEFAULT_STORE = Path("./store")


def main() -> None:
    """Bootstrap the database, register lenses, and start uvicorn."""
    parser = argparse.ArgumentParser(description="UAF Demo Server")
    parser.add_argument(
        "--memory", action="store_true",
        help="Use in-memory database (no persistence)",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Wipe the store directory before starting",
    )
    parser.add_argument(
        "--store", type=Path, default=DEFAULT_STORE,
        help="Path to the store directory (default: ./store)",
    )
    args = parser.parse_args()

    if args.memory:
        from uaf.db.graph_db import GraphDB

        db = GraphDB()
        print("Using in-memory database (no persistence)")
    else:
        from uaf.db.journal import JournaledGraphDB

        db = JournaledGraphDB(args.store, reset=args.reset)
        if args.reset:
            print(f"Store wiped: {args.store}")
        print(f"Using persistent store: {args.store}")

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
