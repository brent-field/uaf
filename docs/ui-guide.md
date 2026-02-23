# UAF Frontend — UI Guide

This document explains how to run and use the HTMX web frontend served at `http://localhost:8000`.

---

## Starting the Server

```bash
uv run python demo.py
```

This boots an in-memory database (no persistence between restarts) and serves the app on port 8000.

---

## Authentication

### Register
1. Open `http://localhost:8000`
2. Click the **Register** tab
3. Enter a **Display Name** and **Password**, then click **Register**
4. You are redirected to the Dashboard

### Login
1. Open `http://localhost:8000/login`
2. Enter the **Principal ID** shown after registration and your **Password**
3. Click **Login**

> **Note:** The Principal ID is a UUID assigned at registration. You can find it displayed in the nav bar after logging in.

### Logout
Click **Logout** in the top-right nav bar. Your session cookie is cleared.

---

## Dashboard

After login you land on the Dashboard (`/dashboard`), which shows:

- **Artifact list** — all documents and spreadsheets you have access to
- **+ New Document** button — creates an empty document and opens the editor
- **Import File** form — upload `.md`, `.csv`, `.txt`, `.docx`, `.pdf`, or Google Docs JSON files

Each artifact row has buttons for:
- **Edit Doc** — open in the document editor
- **Grid View** — open in the spreadsheet viewer
- **Export** — download as Markdown
- **Delete** — remove the artifact (with confirmation)

---

## Document Editor

The editor (`/artifacts/{id}/edit`) provides a Word-like block editing experience.

### Title
- The title field at the top is editable. Change it and press **Tab** or click away to save.

### Toolbar

#### View Mode Toggle
- **Semantic** (default) — clean flowing document with headings, paragraphs, and code blocks. This is the native UAF editing view.
- **Layout** — spatial "desktop publishing" view that preserves the original document layout (text positioning, font properties, page geometry). Available for imported PDFs and DOCX files. Headers and footers are auto-detected and shown with a distinct dashed-border style. Layout view is **read-only** — switch to Semantic to edit.

#### Insert Buttons
- **+ Paragraph** — appends a new paragraph block
- **+ Heading** — appends a new heading block
- **+ Code Block** — appends a new code block

### Editing Blocks
- **Click** on any block's text to open an inline edit form
- **Edit** the text, then click **Save** (or press **Escape** to cancel)
- Changes are saved immediately via HTMX

### Reordering Blocks
- Hover over a block to reveal action buttons on the right
- Click the **up arrow** or **down arrow** to move the block

### Deleting Blocks
- Hover over a block and click the **x** button
- Confirm the deletion in the dialog

### Exporting
- Click **Export .md** in the top-right to download the document as Markdown

### Printing
- Click **Print** in the top-right to open the browser print dialog
- The toolbar, navigation, and action buttons are hidden in print view
- Document content renders cleanly for paper output

---

## Spreadsheet Viewer

The spreadsheet view (`/artifacts/{id}/grid`) displays GridLens-rendered HTML tables.

### Editing Cells
- **Click** any cell to open an edit popup
- Enter the new value and click **Save** (or click Cancel / click outside)
- Numbers are auto-detected (integer or float)

### Adding Rows and Columns
- Click **+ Row** to append a row
- Click **+ Column** to append a column

### Title
- The title field is editable, same as the document editor

### Exporting
- Click **Export .csv** to download as CSV

### Printing
- Click **Print** in the top-right to open the browser print dialog
- Grid content renders cleanly for paper output with hidden UI controls

---

## Importing Files

From the Dashboard:

1. Click **Choose File** and select a file
2. Click **Import** — the format is auto-detected from the file extension

Supported formats:
- **Markdown** (`.md`) — headings, paragraphs, code blocks, lists
- **CSV** (`.csv`) — spreadsheet data
- **Plain Text** (`.txt`) — paragraphs split on blank lines
- **Word** (`.docx`) — paragraphs, headings, and tables; preserves font metadata for Layout view
- **PDF** (`.pdf`) — extracted text blocks with bounding boxes, font properties, and page geometry for Layout view; auto-detects headers/footers
- **Google Docs** (`.json` / `.gdoc`) — Google Docs JSON export format
4. You are redirected to the appropriate editor (document editor for most formats, spreadsheet viewer for CSV)

---

## API Access

The REST API is still available at `/api/...` for programmatic access. All API endpoints require a JWT `Authorization: Bearer <token>` header. See the [Application Layer plan](plans/004-application-layer.md) for full API documentation.

---

## Architecture

The frontend is built with:

- **HTMX** — partial page swaps, no full reloads for editing actions
- **Jinja2** — server-rendered templates
- **Minimal CSS** — no frameworks, custom stylesheet
- **Cookie auth** — JWT stored in an `httponly` cookie, wrapping the same auth system the API uses

Templates live in `src/uaf/app/templates/`, static files in `src/uaf/app/static/`, and route handlers in `src/uaf/app/frontend/routes.py`.
