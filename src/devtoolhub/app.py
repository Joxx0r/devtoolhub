"""FastAPI application factory, health checker, and routes."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from devtoolhub.config import HubConfig, ToolConfig, load_tools_config
from devtoolhub.window import focus_window

logger = logging.getLogger("devtoolhub")

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

POLL_INTERVAL = 10  # seconds
PROBE_TIMEOUT = 3  # seconds


@dataclass
class ToolStatus:
    status: str = "unknown"  # "up", "down", "unknown"
    latency_ms: int = 0
    last_checked: datetime | None = None


@dataclass
class HealthChecker:
    """Background health checker for all configured tools."""

    hub_config: HubConfig
    statuses: dict[str, ToolStatus] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = None
    _http_client: httpx.AsyncClient | None = None

    def start(self) -> None:
        for tool in self.hub_config.tools:
            self.statuses[tool.name] = ToolStatus()
        self._http_client = httpx.AsyncClient(timeout=PROBE_TIMEOUT)
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._http_client:
            await self._http_client.aclose()

    async def _poll_loop(self) -> None:
        # Run first check immediately
        await self._check_all()
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            await self._check_all()

    async def _check_all(self) -> None:
        tasks = [self._check_tool(tool) for tool in self.hub_config.tools]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_tool(self, tool: ToolConfig) -> None:
        strategy = tool.effective_health_check()
        t0 = time.monotonic()
        try:
            if strategy == "http":
                url = tool.health_url or tool.url
                if not url:
                    return
                assert self._http_client is not None
                resp = await self._http_client.get(url)
                up = resp.status_code < 400
            elif strategy == "tcp":
                up = await self._check_tcp(tool)
            elif strategy == "process":
                up = await self._check_process(tool)
            else:
                return
        except Exception:
            up = False

        latency = int((time.monotonic() - t0) * 1000)
        self.statuses[tool.name] = ToolStatus(
            status="up" if up else "down",
            latency_ms=latency,
            last_checked=datetime.now(timezone.utc),
        )

    async def _check_tcp(self, tool: ToolConfig) -> bool:
        url = tool.url
        if not url:
            return False
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=PROBE_TIMEOUT,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    async def _check_process(self, tool: ToolConfig) -> bool:
        pattern = tool.process_pattern
        if not pattern:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                "wmic",
                "process",
                "get",
                "commandline",
                "/format:list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=PROBE_TIMEOUT + 2
            )
            output = stdout.decode("utf-8", errors="replace").lower()
            return pattern.lower() in output
        except (OSError, asyncio.TimeoutError):
            return False

    def get_all_statuses(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, st in self.statuses.items():
            result[name] = {
                "status": st.status,
                "latency_ms": st.latency_ms,
                "last_checked": (
                    st.last_checked.isoformat() if st.last_checked else None
                ),
            }
        return result


def create_app() -> FastAPI:
    hub_config = load_tools_config()
    checker = HealthChecker(hub_config=hub_config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        checker.start()
        yield
        await checker.stop()

    app = FastAPI(title="DevToolHub", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "tools": hub_config.tools,
                "statuses": checker.statuses,
            },
        )

    @app.get("/partials/status", response_class=HTMLResponse)
    async def partials_status(request: Request):
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "tools": hub_config.tools,
                "statuses": checker.statuses,
                "partial": True,
            },
        )

    @app.get("/api/status")
    async def api_status():
        return JSONResponse(checker.get_all_statuses())

    @app.post("/api/focus/{tool_name}")
    async def api_focus(tool_name: str):
        tool = next(
            (t for t in hub_config.tools if t.name == tool_name),
            None,
        )
        if not tool or not tool.window_title:
            return JSONResponse(
                {"ok": False, "message": "Tool not found or no window_title"},
                status_code=404,
            )
        found = focus_window(tool.window_title)
        if found:
            return JSONResponse({"ok": True, "message": "Focused"})
        return JSONResponse(
            {"ok": False, "message": "Window not found"}, status_code=404
        )

    return app
