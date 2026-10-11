"""Command-line entry point: `nexus-mcp [--transport stdio|streamable-http] [--check-config]`."""

from __future__ import annotations

import argparse
import json
import sys

from pydantic import ValidationError

from nexus_mcp.core.config import Settings
from nexus_mcp.core.logging import configure_logging, get_logger
from nexus_mcp.http_app import run_http
from nexus_mcp.server import create_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus-mcp", description=__doc__)
    parser.add_argument("--transport", choices=["stdio", "streamable-http"])
    parser.add_argument("--check-config", action="store_true", help="Validate config and exit")
    args = parser.parse_args(argv)

    try:
        # A --transport flag must pass through the same validators as the environment value.
        settings = Settings(NEXUS_TRANSPORT=args.transport) if args.transport else Settings()
    except ValidationError as exc:
        # Print field names/messages only; never echo input values.
        problems = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        print("Invalid configuration:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 2

    configure_logging(settings.log_level, settings.log_format)
    for warning in settings.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.check_config:
        print(json.dumps(settings.public_summary(), indent=2))
        return 0

    transport = args.transport or settings.transport
    log = get_logger("nexus_mcp")
    log.info("server_starting", **{**settings.public_summary(), "transport": transport})
    server = create_server(settings)
    if transport == "stdio":
        server.run("stdio")
    else:
        run_http(server, settings)
    return 0
