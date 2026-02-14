"""FastAPI application factory and routes."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from devtoolhub.config import load_tools_config
from devtoolhub.health import HealthChecker
from devtoolhub.window import focus_window, launch_process

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    hub_config = load_tools_config()
    checker = HealthChecker(hub_config=hub_config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Run initial check so first page load already has data
        await checker.run_initial_check()
        checker.start_polling()
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
        """Focus a desktop tool's window, or launch it if the window isn't open."""
        tool = next(
            (t for t in hub_config.tools if t.name == tool_name),
            None,
        )
        if not tool or not tool.window_title:
            return JSONResponse(
                {"ok": False, "message": "Tool not found or no window_title"},
                status_code=404,
            )

        # Try to focus existing window
        if focus_window(tool.window_title):
            return JSONResponse({"ok": True, "message": "Focused"})

        # Window not found — launch the GUI if start_command is configured
        if tool.start_command:
            pid = launch_process(
                tool.start_command, cwd=tool.start_cwd, wsl=tool.start_wsl
            )
            if pid:
                return JSONResponse(
                    {"ok": True, "message": f"Opening (PID {pid})"}
                )
            return JSONResponse(
                {"ok": False, "message": "Failed to start"}, status_code=500
            )

        return JSONResponse(
            {"ok": False, "message": "Window not found"}, status_code=404
        )

    @app.post("/api/start/{tool_name}")
    async def api_start(tool_name: str):
        """Start a tool using its configured start_command."""
        tool = next(
            (t for t in hub_config.tools if t.name == tool_name),
            None,
        )
        if not tool or not tool.start_command:
            return JSONResponse(
                {"ok": False, "message": "Tool not found or no start_command"},
                status_code=404,
            )

        pid = launch_process(
            tool.start_command, cwd=tool.start_cwd, wsl=tool.start_wsl
        )
        if pid:
            return JSONResponse({"ok": True, "message": f"Started (PID {pid})"})
        return JSONResponse(
            {"ok": False, "message": "Failed to start"}, status_code=500
        )

    return app
