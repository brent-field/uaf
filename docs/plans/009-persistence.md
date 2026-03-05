# Plan 009: Persistence Layer

**Status:** Implemented

---

## Overview

Add data persistence to UAF so that graph state, security state, and binary blobs
survive process restarts. The implementation uses an append-only JSONL journal
(endorsed in Plan 002, Appendix C2) with a wrapper pattern that leaves the existing
in-memory `GraphDB` completely unchanged.

---

## Store Layout

```
<store_root>/                  # Configurable via UAF_STORE_DIR env var
    operations.jsonl           # One JSON line per operation (append-only)
    security.jsonl             # Security events: principals, ACLs, audit
    blobs/                     # Content-addressed binary files
        <sha256-hex>           # Raw bytes, filename = BlobId
    META.json                  # Store version + operation count
```

---

## Architecture: Wrapper Pattern

`JournaledGraphDB` wraps `GraphDB` via composition. No changes to GraphDB internals.
All existing tests pass unchanged.

```
JournaledGraphDB
    |-- Store (directory, journal, blobs, metadata)
    |     |-- Journal (operations.jsonl)
    |     +-- blobs/
    +-- GraphDB (in-memory, unchanged)
          |-- OperationLog
          |-- StateMaterializer
          |-- EAVTIndex
          +-- QueryEngine
```

**On `apply(op)`:** Write operation to journal FIRST (write-ahead), then apply to
in-memory GraphDB.

**On construction:** Replay journal by calling `GraphDB.apply(op)` for each line,
rebuilding MaterializedState + EAVT + QueryEngine.

**Crash safety:** If the process dies mid-write, the last partial JSON line is skipped
on replay.

---

## Security State Persistence

Security events (principal creation, ACL grants, parent registration) happen at the
`SecureGraphDB` level, not in the operation log. A separate `security.jsonl` records
these events.

`SecureGraphDB` accepts an optional `on_security_event` callback. `SecurityStore`
provides this callback and writes events to `security.jsonl`. On startup,
`SecurityStore.replay()` rebuilds `LocalAuthProvider` and `PermissionResolver` state.

---

## New Files

| File | Purpose |
|------|---------|
| `src/uaf/db/journal.py` | JSONL journal: append, read_all, truncate |
| `src/uaf/db/store.py` | Store directory manager: journal + blobs + metadata + delete_all |
| `src/uaf/db/journaled_graph_db.py` | `JournaledGraphDB` wrapper around `GraphDB` |
| `src/uaf/security/security_store.py` | Persist/replay security events |
| `tests/uaf/db/test_journal.py` | Journal unit tests (16 tests) |
| `tests/uaf/db/test_store.py` | Store unit tests (15 tests) |
| `tests/uaf/db/test_journaled_graph_db.py` | Integration tests (12 tests) |
| `tests/uaf/security/test_security_store.py` | Security persistence tests (5 tests) |
| `tests/uaf/db/test_persistence_bench.py` | Performance benchmarks (6 tests) |

## Modified Files

| File | Change |
|------|--------|
| `src/uaf/security/secure_graph_db.py` | Accept `GraphDB \| JournaledGraphDB`; optional `on_security_event` callback |
| `src/uaf/app/formats/*.py` | Accept `GraphDB \| JournaledGraphDB` in all handler methods |
| `src/uaf/db/__init__.py` | Export `Journal`, `JournalConfig`, `JournaledGraphDB`, `Store`, `StoreConfig` |
| `src/uaf/security/__init__.py` | Export `SecurityStore` |
| `Makefile` | Add `make bench` and `make reset-store` targets |
| `pyproject.toml` | Add `benchmark` marker, exclude from default test runs |

---

## Dev-Mode Store Delete

`Store.delete_all()` wipes the entire store directory via `shutil.rmtree`. Available via:

- `Store.delete_all()` for programmatic use
- `make reset-store` Makefile target

---

## Performance Baseline

Benchmarks measured on Apple Silicon (run with `make bench`):

| Metric | Result |
|--------|--------|
| Write overhead (journaled vs in-memory) | ~1.7x |
| Replay throughput (1K ops) | ~40K ops/sec |
| Replay throughput (5K ops) | ~37K ops/sec |
| Blob write (1KB) | ~15 MB/s |
| Blob write (100KB) | ~525 MB/s |
| Blob write (1MB) | ~1.3 GB/s |
| Blob read (100KB, cold) | ~3.5 GB/s |
| Journal bytes per operation | ~373 bytes |

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| JSONL, not SQLite | Endorsed by existing plan; no new deps; human-readable |
| Wrapper, not modification | Existing code/tests untouched |
| Write-ahead journal | Crash safety: op on disk before in-memory |
| Separate security.jsonl | Security state not in operation log; clean separation |
| Content-addressed blob files | Idempotent writes; matches existing BlobId design |
| No new dependencies | stdlib only (json, pathlib, shutil) |

---

## Future: SQLite Migration

The JSONL journal is intentionally simple for V1. When the graph exceeds ~100K operations,
the sequential replay on startup becomes a bottleneck. At that point, migrate to SQLite:

1. Operations table replaces `operations.jsonl`
2. Materialized state lives in indexed columns
3. EAVT indexes become SQL indexes
4. Replay is replaced by SQL queries
5. The `JournaledGraphDB` wrapper API stays the same
