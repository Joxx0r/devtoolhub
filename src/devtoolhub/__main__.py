"""DevToolHub - centralized developer tool dashboard."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="DevToolHub")
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch the web dashboard instead of the TUI",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Web server port (default: from DEVTOOLHUB_PORT or 41001)",
    )
    args = parser.parse_args()

    if args.web:
        import uvicorn

        from devtoolhub.config import get_settings

        settings = get_settings()
        port = args.port or settings.port
        uvicorn.run(
            "devtoolhub.app:create_app",
            factory=True,
            host="127.0.0.1",
            port=port,
            log_level="info",
        )
    else:
        from devtoolhub.tui import run_tui

        run_tui()


if __name__ == "__main__":
    main()
