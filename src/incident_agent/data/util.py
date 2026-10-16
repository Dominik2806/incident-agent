"""Small helpers for building realistic identifiers and stack traces."""

import random
from datetime import datetime

from incident_agent.data.constants import SERVICES
from incident_agent.data.models import ErrorRow, GeneratedData, LogRow

CLIENT_VERSIONS = ("web-2.31.0", "web-2.30.4", "ios-5.8.1", "android-5.7.9")

Frame = tuple[str, int, str, str]  # (file, line, function, source line)


def new_request_id(rng: random.Random) -> str:
    return f"req-{rng.getrandbits(40):010x}"


def format_traceback(frames: list[Frame], exception_type: str, message: str) -> str:
    lines = ["Traceback (most recent call last):"]
    for file, line, function, source in frames:
        lines.append(f'  File "{file}", line {line}, in {function}')
        lines.append(f"    {source}")
    lines.append(f"{exception_type}: {message}")
    return "\n".join(lines)


def make_error(
    timestamp: datetime,
    service: str,
    exception_type: str,
    message: str,
    frames: list[Frame],
    request_id: str,
    endpoint: str,
    client_version: str,
    http_status: int = 500,
) -> ErrorRow:
    assert service in SERVICES
    return ErrorRow(
        timestamp=timestamp,
        service=service,
        exception_type=exception_type,
        message=message,
        stacktrace=format_traceback(frames, exception_type, message),
        request_context={
            "request_id": request_id,
            "endpoint": endpoint,
            "client_version": client_version,
            "http_status": http_status,
        },
    )


def add_error_log(data: GeneratedData, error: ErrorRow, log_message: str) -> int:
    """Store an error plus the ERROR log line that points to it. Returns the provisional id."""
    error_id = data.add_error(error)
    data.logs.append(
        LogRow(
            timestamp=error.timestamp,
            service=error.service,
            level="ERROR",
            message=log_message,
            request_id=str(error.request_context["request_id"]),
            error_id=error_id,
        )
    )
    return error_id
