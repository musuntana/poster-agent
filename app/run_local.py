"""Supported local-only Uvicorn launcher for the unauthenticated demo.

This guard applies to the project's documented launcher. Running Uvicorn
directly can bypass it, so raw Uvicorn and proxy/container exposure remain
unsupported until authentication and resource authorization are implemented.
"""
from __future__ import annotations

import argparse
import ipaddress
from collections.abc import Sequence

import uvicorn


def validate_loopback_host(host: str) -> str:
    """Return a canonical loopback IP or reject network-exposed bind addresses."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            "Only a numeric loopback IP (127.0.0.1 or ::1) is supported; "
            "network binding is disabled."
        ) from exc
    if not address.is_loopback:
        raise ValueError(
            "Only a loopback IP (127.0.0.1 or ::1) is supported; "
            "binding to a network interface is disabled."
        )
    return str(address)


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the unauthenticated Poster Agent demo on loopback only."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="numeric loopback IP only (default: 127.0.0.1)",
    )
    parser.add_argument("--port", type=parse_port, default=8000)
    parser.add_argument("--reload", action="store_true", help="enable local development reload")
    args = parser.parse_args(argv)

    try:
        host = validate_loopback_host(args.host)
    except ValueError as exc:
        parser.error(str(exc))

    # Use an import string so Uvicorn's reload worker can import the app.
    # Never trust X-Forwarded-* headers in this local-only demo.
    uvicorn.run(
        "app.main:app",
        host=host,
        port=args.port,
        reload=args.reload,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
