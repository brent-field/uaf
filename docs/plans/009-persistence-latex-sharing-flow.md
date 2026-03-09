# Plan 009 — Persistence, LaTeX, Sharing, FlowLens, UI Strategy

## Summary

Addresses five gaps in the UAF system:

1. **Persistence** — JSONL append-only journal + file-backed blob store
2. **LaTeX support** — Import/export `.tex` files via `pylatexenc` AST parsing
3. **Sharing** — `.uaf` portable bundle format (zip with manifest, journal, blobs)
4. **FlowLens** — Project management lens with Gantt, dependency, DAG, and Kanban views
5. **Editor polish** — Keyboard shortcuts, type badges, multi-format export
6. **UI strategy** — Demo vs. commercial UI separation, uaf-premium scaffolding

## Phase 1: JSONL Persistence

`JournaledGraphDB` extends `GraphDB` with persistence. On init: create dirs, replay journal. Overrides `apply()` to append operations as JSONL. `FileBlobStore` uses content-addressed files.

- **Files:** `src/uaf/db/journal.py`, `tests/uaf/db/test_journal.py`, `tests/uaf/db/test_journal_bench.py`
- **Modified:** `demo.py` (use JournaledGraphDB), `Makefile` (bench/reset targets)
- **Dev tools:** `reset()` wipes all data, `delete_artifact()` removes subtrees, `--reset` CLI flag

## Phase 2: Editor Polish

Improved HTMX editor UX:

- Enter-to-save, Escape-to-cancel, Ctrl+Enter for code blocks
- Auto-focus and select on click-to-edit
- Artifact type badges (Doc/Sheet/Project) on dashboard
- Multi-format export dropdown (.md, .txt, .tex, .docx)

## Phase 3: LaTeX Format Handler

`LatexHandler` imports/exports `.tex` files using `pylatexenc` for AST parsing.

| LaTeX | Node Type |
|---|---|
| `\section{X}` | `Heading(level=1)` |
| `\begin{equation}` | `MathBlock(display="block")` |
| `$...$` | `MathBlock(display="inline")` |
| `\begin{verbatim}` | `CodeBlock` |
| `\begin{itemize}` | `TextBlock(format="latex")` |

- **Files:** `src/uaf/app/formats/latex.py`, `tests/uaf/app/test_latex_handler.py`, `tests/fixtures/latex/`
- **Dependency:** `pylatexenc>=2.10`

## Phase 4: Sharing & Distribution

`.uaf` bundle format — zip containing `manifest.json`, `journal.jsonl`, `blobs/`.

- **Full mode:** Preserves operation history (filtered to subtree)
- **Snapshot mode:** Generates synthetic ops for current state only
- **API:** `GET /api/sharing/artifacts/{id}/bundle`, `POST /api/sharing/artifacts/import-bundle`
- **Files:** `src/uaf/db/bundle.py`, `src/uaf/app/api/routes_sharing.py`

## Phase 5: FlowLens

Project management lens with four view modes:

- **Gantt:** Task list (left) + time bars (right), milestones for due-date-only tasks
- **Dependencies:** Horizontal arrows between predecessor/successor rows
- **DAG:** Topologically sorted node-and-edge graph
- **Kanban:** Three columns by status (To Do / In Progress / Done)

Extended `Task` node with `start_date`, `end_date`, `status`. Circular dependency detection via graph traversal. WBS hierarchy via existing CONTAINS edges.

- **Files:** `src/uaf/app/lenses/flow_lens.py`, `tests/uaf/app/test_flow_lens.py`
- **Actions:** CreateTask, CreateTaskGroup, UpdateTask, ToggleTask, SetDependency, RemoveDependency, SetDueDate, SetDateRange

## Phase 6: UI Strategy

Demo HTMX frontend serves as proof-of-concept. Commercial UI planned as separate `uaf-premium` package:

- React/SvelteKit consuming FastAPI `/api/` endpoints
- Lens switcher as primary navigation
- Minimal chrome (iA Writer / Typora inspired)
- API split already exists: `src/uaf/app/api/` (JSON) vs `src/uaf/app/frontend/` (HTMX)

## Test Coverage

~150+ new tests across 8 new test files. All phases follow TDD (tests written first).
