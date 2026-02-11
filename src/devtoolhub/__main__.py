"""DevToolHub - centralized developer tool dashboard."""

from __future__ import annotations

import sys

import uvicorn

from devtoolhub.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "devtoolhub.app:create_app",
        factory=True,
        host="127.0.0.1",
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
