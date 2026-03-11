"""Pydantic request/response models for the REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    display_name: str
    password: str


class RegisterRequest(BaseModel):
    display_name: str
    password: str


class TokenResponse(BaseModel):
    token: str
    principal_id: str
    display_name: str


class PrincipalResponse(BaseModel):
    principal_id: str
    display_name: str


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class CreateArtifactRequest(BaseModel):
    title: str


class ArtifactResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    child_count: int = 0


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactResponse]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


class NodeResponse(BaseModel):
    id: str
    node_type: str
    fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class UpdateNodeRequest(BaseModel):
    fields: dict[str, Any]


class ChildrenResponse(BaseModel):
    parent_id: str
    children: list[NodeResponse]


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


class CreateEdgeRequest(BaseModel):
    source: str
    target: str
    edge_type: str


class EdgeResponse(BaseModel):
    id: str
    source: str
    target: str
    edge_type: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Lens
# ---------------------------------------------------------------------------


class LensViewResponse(BaseModel):
    lens_type: str
    artifact_id: str
    title: str
    content: str
    content_type: str
    node_count: int


class LensActionRequest(BaseModel):
    action_type: str
    params: dict[str, Any]


# ---------------------------------------------------------------------------
# ACL
# ---------------------------------------------------------------------------


class GrantRoleRequest(BaseModel):
    principal_id: str
    role: str


class ACLEntryResponse(BaseModel):
    principal_id: str
    role: str
    granted_at: datetime
    granted_by: str


class ACLResponse(BaseModel):
    artifact_id: str
    entries: list[ACLEntryResponse]
    public_read: bool


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchResponse(BaseModel):
    results: list[NodeResponse]


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class HistoryEntryResponse(BaseModel):
    operation_id: str
    operation_type: str
    timestamp: datetime
    principal_id: str | None


class HistoryResponse(BaseModel):
    node_id: str
    entries: list[HistoryEntryResponse]
