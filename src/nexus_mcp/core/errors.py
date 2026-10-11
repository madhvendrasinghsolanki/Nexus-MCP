"""Error taxonomy. Every expected failure maps to a stable code and a client-safe message.

Internal details (stack traces, upstream bodies, credentials) are logged, never returned.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    CONFIGURATION = "configuration_error"
    VALIDATION = "validation_error"
    NOT_FOUND = "not_found"
    PROVIDER = "provider_error"
    TIMEOUT = "upstream_timeout"
    WRITE_NOT_PERMITTED = "write_not_permitted"
    CONFIRMATION_REQUIRED = "confirmation_required"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal_error"


class NexusError(Exception):
    """Base class for expected, client-presentable errors."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_client_message(self) -> str:
        text = f"[{self.code.value}] {self.message}"
        return f"{text} Hint: {self.hint}" if self.hint else text


class ConfigurationError(NexusError):
    code = ErrorCode.CONFIGURATION


class ValidationFailed(NexusError):
    code = ErrorCode.VALIDATION


class NotFound(NexusError):
    code = ErrorCode.NOT_FOUND


class ProviderError(NexusError):
    code = ErrorCode.PROVIDER


class UpstreamTimeout(NexusError):
    code = ErrorCode.TIMEOUT


class WriteNotPermitted(NexusError):
    code = ErrorCode.WRITE_NOT_PERMITTED


class ConfirmationRequired(NexusError):
    code = ErrorCode.CONFIRMATION_REQUIRED


class RateLimited(NexusError):
    code = ErrorCode.RATE_LIMITED
