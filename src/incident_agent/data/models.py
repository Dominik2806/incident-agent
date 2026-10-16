"""Plain data containers for generated rows."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class LogRow:
    timestamp: datetime
    service: str
    level: str
    message: str
    request_id: str | None = None
    error_id: int | None = None


@dataclass
class DeploymentRow:
    timestamp: datetime
    service: str
    version: str
    author: str
    commit_message: str


@dataclass
class ErrorRow:
    timestamp: datetime
    service: str
    exception_type: str
    message: str
    stacktrace: str
    request_context: dict[str, str | int]


@dataclass
class CheckoutRequest:
    timestamp: datetime
    request_id: str
    client_version: str


@dataclass
class GeneratedData:
    """Everything the generator produces, before ids are assigned and rows are written."""

    logs: list[LogRow] = field(default_factory=list)
    deployments: list[DeploymentRow] = field(default_factory=list)
    errors: list[ErrorRow] = field(default_factory=list)
    checkout_requests: list[CheckoutRequest] = field(default_factory=list)

    def add_error(self, error: ErrorRow) -> int:
        """Store an error and return its provisional id (renumbered chronologically later)."""
        self.errors.append(error)
        return len(self.errors)
