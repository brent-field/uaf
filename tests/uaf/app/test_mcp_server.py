"""Tests for the MCP server — tool registration and invocation."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from uaf.app.lenses import LensRegistry
from uaf.app.lenses.doc_lens import DocLens
from uaf.app.lenses.grid_lens import GridLens
from uaf.app.mcp_server import create_mcp_server
from uaf.db.graph_db import GraphDB
from uaf.security.auth import LocalAuthProvider
from uaf.security.secure_graph_db import SecureGraphDB


def _setup() -> tuple[SecureGraphDB, Any]:
    db = GraphDB()
    auth = LocalAuthProvider()
    sdb = SecureGraphDB(db, auth)
    registry = LensRegistry()
    registry.register(DocLens())
    registry.register(GridLens())
    session = sdb.system_session()
    mcp = create_mcp_server(sdb, session, registry)
    return sdb, mcp


def _call(mcp: Any, name: str, args: dict[str, object]) -> dict[str, Any]:
    """Call an MCP tool and parse the JSON result."""
    content_blocks, _raw = asyncio.run(mcp.call_tool(name, args))
    text: str = content_blocks[0].text
    return json.loads(text)  # type: ignore[no-any-return]


def _call_text(mcp: Any, name: str, args: dict[str, object]) -> str:
    """Call an MCP tool and return raw text result."""
    content_blocks, _raw = asyncio.run(mcp.call_tool(name, args))
    return content_blocks[0].text  # type: ignore[no-any-return]


class TestToolRegistration:
    def test_tools_registered(self) -> None:
        _, mcp = _setup()
        tools = asyncio.run(mcp.list_tools())
        tool_names = {t.name for t in tools}
        expected = {
            "create_artifact", "get_artifact", "add_child", "get_node",
            "get_children", "update_node", "delete_node", "find_by_type",
            "search", "get_references_to", "get_history",
            "render_artifact", "import_file", "export_file",
        }
        assert expected.issubset(tool_names)


class TestMCPTools:
    def test_create_artifact(self) -> None:
        _, mcp = _setup()
        result = _call(mcp, "create_artifact", {"title": "Test Doc"})
        assert "artifact_id" in result
        assert result["title"] == "Test Doc"

    def test_get_artifact(self) -> None:
        _, mcp = _setup()
        create_result = _call(mcp, "create_artifact", {"title": "GA"})
        aid = create_result["artifact_id"]

        result = _call(mcp, "get_artifact", {"artifact_id": aid})
        assert result["child_count"] == 0

    def test_get_artifact_not_found(self) -> None:
        _, mcp = _setup()
        from uaf.core.node_id import NodeId

        fake = str(NodeId.generate())
        result = _call(mcp, "get_artifact", {"artifact_id": fake})
        assert "error" in result

    def test_add_child_paragraph(self) -> None:
        _, mcp = _setup()
        art = _call(mcp, "create_artifact", {"title": "AC"})
        aid = art["artifact_id"]

        child = _call(
            mcp, "add_child",
            {"parent_id": aid, "node_type": "paragraph", "text": "Hello"},
        )
        assert "node_id" in child

        children_text = _call_text(mcp, "get_children", {"node_id": aid})
        children = json.loads(children_text)
        assert len(children) == 1

    def test_get_node(self) -> None:
        _, mcp = _setup()
        art = _call(mcp, "create_artifact", {"title": "GN"})
        aid = art["artifact_id"]

        result = _call(mcp, "get_node", {"node_id": aid})
        assert result.get("title") == "GN"

    def test_update_node(self) -> None:
        _, mcp = _setup()
        art = _call(mcp, "create_artifact", {"title": "UN"})
        aid = art["artifact_id"]

        _call(mcp, "update_node", {"node_id": aid, "fields": {"title": "Updated"}})
        result = _call(mcp, "get_node", {"node_id": aid})
        assert result.get("title") == "Updated"

    def test_delete_node(self) -> None:
        _, mcp = _setup()
        art = _call(mcp, "create_artifact", {"title": "DN"})
        aid = art["artifact_id"]

        child = _call(
            mcp, "add_child",
            {"parent_id": aid, "node_type": "paragraph", "text": "Del"},
        )
        cid = child["node_id"]

        _call(mcp, "delete_node", {"node_id": cid})
        result = _call(mcp, "get_node", {"node_id": cid})
        assert "error" in result

    def test_find_by_type(self) -> None:
        _, mcp = _setup()
        _call(mcp, "create_artifact", {"title": "FBT1"})
        _call(mcp, "create_artifact", {"title": "FBT2"})

        text = _call_text(mcp, "find_by_type", {"node_type": "artifact"})
        results = json.loads(text)
        assert len(results) >= 2

    def test_search(self) -> None:
        _, mcp = _setup()
        _call(mcp, "create_artifact", {"title": "SearchTarget"})

        text = _call_text(mcp, "search", {"attribute": "title", "value": "SearchTarget"})
        results = json.loads(text)
        assert len(results) >= 1

    def test_get_history(self) -> None:
        _, mcp = _setup()
        art = _call(mcp, "create_artifact", {"title": "H"})
        aid = art["artifact_id"]

        text = _call_text(mcp, "get_history", {"node_id": aid})
        entries = json.loads(text)
        assert len(entries) >= 1

    def test_render_artifact(self) -> None:
        _, mcp = _setup()
        art = _call(mcp, "create_artifact", {"title": "Render"})
        aid = art["artifact_id"]

        result = _call(mcp, "render_artifact", {"artifact_id": aid, "lens_type": "doc"})
        assert result["lens_type"] == "doc"
        assert "Render" in result["content"]

    def test_render_unknown_lens(self) -> None:
        _, mcp = _setup()
        art = _call(mcp, "create_artifact", {"title": "X"})
        aid = art["artifact_id"]

        result = _call(mcp, "render_artifact", {"artifact_id": aid, "lens_type": "bogus"})
        assert "error" in result

    def test_import_export(self) -> None:
        _, mcp = _setup()
        md_content = "# Hello\n\nWorld\n"

        imp = _call(
            mcp, "import_file",
            {"file_content": md_content, "filename": "test.md", "format": "markdown"},
        )
        aid = imp["artifact_id"]

        exported = _call_text(
            mcp, "export_file", {"artifact_id": aid, "format": "markdown"},
        )
        assert "Hello" in exported
        assert "World" in exported

    def test_import_unknown_format(self) -> None:
        _, mcp = _setup()
        result = _call(
            mcp, "import_file",
            {"file_content": "x", "filename": "x.docx", "format": "docx"},
        )
        assert "error" in result
