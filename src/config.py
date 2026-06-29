import argparse
import os
import sys
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Configuration data class."""

    paprika_username: str
    paprika_password: str


def get_config() -> Config:
    """
    Load configuration from environment variables or command line arguments.

    Priority order:
    1. Command line arguments
    2. Environment variables
    3. Interactive prompt (for development)

    Returns:
        Config object with all required settings

    Raises:
        SystemExit: If required configuration is missing
    """
    parser = argparse.ArgumentParser(description="Paprika MCP Server")
    parser.add_argument("--username", help="Paprika account username (email)")
    parser.add_argument("--password", help="Paprika account password")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run as HTTP server (Streamable HTTP + legacy SSE). Default is stdio.",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Alias for --http (kept for backward compatibility).",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    parser.add_argument(
        "--lan-host",
        help="Optional second, unauthenticated listener bound to this LAN host "
        "(e.g. for the Home Assistant MCP integration). Keep off the public proxy.",
    )
    parser.add_argument(
        "--lan-port",
        type=int,
        help="Port for the unauthenticated LAN listener (used with --lan-host).",
    )
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Make the PRIMARY listener unauthenticated (pure-LAN deployment).",
    )
    parser.add_argument(
        "--base-path",
        default="",
        help=(
            "Public URL prefix when behind a reverse proxy that strips a "
            "path (e.g. /paprika). Required for SSE clients only."
        ),
    )

    args = parser.parse_args()

    # Get credentials from args, environment, or prompt
    username = (
        args.username
        or os.environ.get("PAPRIKA_USERNAME")
        or _prompt_for_input("Paprika username (email): ")
    )

    password = (
        args.password
        or os.environ.get("PAPRIKA_PASSWORD")
        or _prompt_for_input("Paprika password: ", secret=True)
    )

    if not username or not password:
        print("Error: Paprika username and password are required", file=sys.stderr)
        print(
            "Set via environment variables or command line arguments:", file=sys.stderr
        )
        print("  PAPRIKA_USERNAME=your_email@example.com", file=sys.stderr)
        print("  PAPRIKA_PASSWORD=your_password", file=sys.stderr)
        print(
            "  python server.py --username your_email@example.com --password your_password",
            file=sys.stderr,
        )
        sys.exit(1)

    return Config(paprika_username=username, paprika_password=password)


def _prompt_for_input(prompt: str, secret: bool = False) -> Optional[str]:
    """
    Prompt user for input (only when running interactively).

    Args:
        prompt: Prompt message to display
        secret: Whether to hide input (for passwords)

    Returns:
        User input or None if not interactive
    """
    # Only prompt if running interactively (not via MCP)
    if not sys.stdin.isatty() or "--help" in sys.argv:
        return None

    try:
        if secret:
            import getpass

            return getpass.getpass(prompt)
        else:
            return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None
