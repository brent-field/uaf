"""Core identifiers — NodeId, EdgeId, OperationId, BlobId, and utc_now."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class NodeId:
    """Unique identifier for a node, wrapping a UUID."""

    value: uuid.UUID

    @classmethod
    def generate(cls) -> NodeId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class EdgeId:
    """Unique identifier for an edge, wrapping a UUID."""

    value: uuid.UUID

    @classmethod
    def generate(cls) -> EdgeId:
        return cls(value=uuid.uuid4())

    def __str__(self) -> str:
        return str(self.value)


_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class OperationId:
    """Content-addressed operation identifier — SHA-256 hex digest."""

    hex_digest: str

    def __post_init__(self) -> None:
        if not _HEX64_RE.match(self.hex_digest):
            msg = f"OperationId must be a 64-char lowercase hex string, got: {self.hex_digest!r}"
            raise ValueError(msg)

    def __str__(self) -> str:
        return self.hex_digest


@dataclass(frozen=True, slots=True)
class BlobId:
    """Content-addressed blob identifier — SHA-256 hex digest of raw bytes."""

    hex_digest: str

    def __post_init__(self) -> None:
        if not _HEX64_RE.match(self.hex_digest):
            msg = f"BlobId must be a 64-char lowercase hex string, got: {self.hex_digest!r}"
            raise ValueError(msg)

    def __str__(self) -> str:
        return self.hex_digest


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=UTC)
