"""UAF error hierarchy — shared by database and security layers."""


class UAFError(Exception):
    """Base exception for all UAF errors."""


class NodeNotFoundError(UAFError):
    """Raised when a node lookup fails on a missing ID."""


class EdgeNotFoundError(UAFError):
    """Raised when an edge lookup fails on a missing ID."""


class InvalidEdgeError(UAFError):
    """Raised when an edge violates a graph constraint."""


class DuplicateOperationError(UAFError):
    """Raised when appending an operation with an existing hash."""


class InvalidParentError(UAFError):
    """Raised when an operation references non-existent parent operations."""


class SerializationError(UAFError):
    """Raised on unknown __type__, corrupt data, or deserialization failures."""


class PermissionDeniedError(UAFError):
    """Raised when an operation is denied by access control."""


class AuthenticationError(UAFError):
    """Raised when authentication fails."""
