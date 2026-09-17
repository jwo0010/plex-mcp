"""Command-line entry point: `plex-mcp` or `python -m plex_mcp`."""

from __future__ import annotations

import argparse
import logging
import sys

from pydantic import ValidationError

from plex_mcp import __version__


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="plex-mcp", description="MCP server for Plex Media Server.")
    parser.add_argument(
        "--transport",
        choices=["http", "stdio"],
        default="http",
        help="http: Streamable HTTP server for network clients (default). stdio: for a client that launches this process.",
    )
    parser.add_argument("--env-file", help="Path to a .env file (default: .env in the current directory).")
    parser.add_argument("--host", help="Override PLEX_MCP_HOST.")
    parser.add_argument("--port", type=int, help="Override PLEX_MCP_PORT.")
    parser.add_argument("--read-only", action="store_true", help="Only register read tools.")
    parser.add_argument("--check", action="store_true", help="Test the Plex connection and exit.")
    parser.add_argument("--version", action="version", version=f"plex-mcp {__version__}")
    args = parser.parse_args(argv)

    from plex_mcp.config import load_settings

    try:
        settings = load_settings(args.env_file)
    except ValidationError as exc:
        missing = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        sys.exit(f"Configuration error ({', '.join(missing)}): set PLEX_TOKEN (and PLEX_URL) in the environment or .env.\n{exc}")

    overrides = {k: v for k, v in {"host": args.host, "port": args.port}.items() if v is not None}
    if args.read_only:
        overrides["read_only"] = True
    if overrides:
        settings = settings.model_copy(update=overrides)

    # Logs go to stderr (never stdout, which is the stdio transport's wire) and optionally to a file.
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if settings.log_file:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path

        Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(settings.log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"))
    logging.basicConfig(
        level=settings.log_level,
        handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("plexapi").setLevel(max(logging.WARNING, logging.getLevelName(settings.log_level)))
    log = logging.getLogger("plex_mcp")

    if args.check:
        from plex_mcp.plex import PlexContext

        ctx = PlexContext(settings)
        plex = ctx.server
        print(f"Connected to '{plex.friendlyName}' (Plex {plex.version})")
        for section in ctx.sections():
            print(f"  [{section.key}] {section.title} ({section.type})")
        return

    from plex_mcp.server import build_server

    mcp = build_server(settings)

    if args.transport == "stdio":
        log.info("plex-mcp %s on stdio (read_only=%s)", __version__, settings.read_only)
        mcp.run("stdio")
        return

    import uvicorn

    from plex_mcp.http_app import build_http_app, check_http_safety

    check_http_safety(settings)
    app = build_http_app(mcp, settings)
    log.info(
        "plex-mcp %s listening on http://%s:%s%s (auth=%s, read_only=%s, deletes=%s)",
        __version__, settings.host, settings.port, settings.path,
        "bearer" if settings.auth_token else "none", settings.read_only,
        settings.allow_delete and not settings.read_only,
    )
    # log_config=None routes uvicorn's logs through the handlers configured above.
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower(), log_config=None)


if __name__ == "__main__":
    main()
