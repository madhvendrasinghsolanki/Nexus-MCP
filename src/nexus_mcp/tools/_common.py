"""Shared plumbing for tool modules: app container, the `guarded` boundary, annotations."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from nexus_mcp.core.config import Settings
from nexus_mcp.core.errors import NexusError, UpstreamTimeout
from nexus_mcp.core.logging import get_logger
from nexus_mcp.core.ratelimit import RateLimiter
from nexus_mcp.providers.registry import ProviderRegistry
from nexus_mcp.services.audit import AuditLog

log = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class App:
    """Dependency container handed to every tool module."""

    settings: Settings
    registry: ProviderRegistry
    limiter: RateLimiter
    audit: AuditLog


@dataclass(frozen=True)
class Audit:
    """Declares a tool as a write and says how to describe it in the audit trail."""

    source: str
    operation: str
    target_arg: str  # name of the tool argument that identifies what is being changed


READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
READ_ONLY_LOCAL = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, open_world_hint=False
)


def safe_tool(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Convert expected errors into clean ToolErrors; hide the details of unexpected ones."""

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await fn(*args, **kwargs)
        except NexusError as exc:
            log.info("tool_error", tool=fn.__name__, code=exc.code.value)
            raise ToolError(exc.to_client_message()) from None
        except ToolError:
            raise
        except Exception as exc:
            log.error("tool_crash", tool=fn.__name__, error=type(exc).__name__, exc_info=True)
            raise ToolError("[internal_error] The tool failed unexpectedly.") from None

    return wrapper


def guarded(
    app: App, audit: Audit | None = None
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Every tool goes through this: rate limit, timeout (reads), audit (writes), error boundary.

    Pass `audit=Audit(...)` for write tools. Real (non-dry-run) writes are recorded BEFORE they run
    and the write is refused if the record cannot be stored.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            tool = fn.__name__
            if audit is None:
                app.limiter.check("read")
                try:
                    return await asyncio.wait_for(
                        fn(*args, **kwargs), timeout=app.settings.tool_timeout_s
                    )
                except TimeoutError:
                    raise UpstreamTimeout("The tool timed out.") from None

            target = kwargs.get(audit.target_arg)
            dry_run = bool(kwargs.get("dry_run", True))
            fields: dict[str, Any] = {
                "tool": tool,
                "source": audit.source,
                "operation": audit.operation,
                "target": None if target is None else str(target)[:200],
                "dry_run": dry_run,
            }
            try:
                app.limiter.check("write")
                if not dry_run:
                    app.audit.record("intent", **fields)
                result = await fn(*args, **kwargs)
            except NexusError as exc:
                outcome = (
                    "denied" if exc.code.value.endswith(("permitted", "required")) else "error"
                )
                _safe_audit(app, "result", fields, outcome=outcome, error_code=exc.code.value)
                raise
            except Exception:
                _safe_audit(app, "result", fields, outcome="error", error_code="internal_error")
                raise
            affected = getattr(result, "affected_count", None)
            _safe_audit(app, "result", fields, outcome="ok", affected=affected)
            return result

        return safe_tool(inner)

    return decorator


def _safe_audit(app: App, phase: str, fields: dict[str, Any], **extra: Any) -> None:
    """Result records must never mask the real outcome of a write."""
    try:
        app.audit.record(phase, **fields, **extra)
    except NexusError:
        log.error("audit_result_not_persisted", tool=fields["tool"])
