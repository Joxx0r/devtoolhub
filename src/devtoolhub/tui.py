"""Textual TUI for DevToolHub."""

from __future__ import annotations

import asyncio
import webbrowser

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static

from devtoolhub.config import ToolConfig, load_tools_config
from devtoolhub.health import POLL_INTERVAL, HealthChecker, ToolStatus
from devtoolhub.window import focus_window, launch_process


class ConfirmStartScreen(ModalScreen[bool]):
    """Modal dialog to confirm restarting an already-running service."""

    CSS = """
    ConfirmStartScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: 50;
        height: auto;
        background: #24283b;
        border: thick #f7768e;
        padding: 1 2;
    }
    #confirm-title {
        text-style: bold;
        color: #f7768e;
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    #confirm-message {
        color: #c0caf5;
        width: 100%;
        margin-bottom: 1;
    }
    #confirm-buttons {
        width: 100%;
        align: center middle;
        height: 3;
    }
    #confirm-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__()
        self.tool_name = tool_name

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label("Warning", id="confirm-title")
            yield Label(
                f"{self.tool_name} is already running.\nStart another instance?",
                id="confirm-message",
            )
            with Horizontal(id="confirm-buttons"):
                yield Button("Start Anyway", variant="error", id="confirm-yes")
                yield Button("Cancel", variant="default", id="confirm-no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")


class DevToolHubTUI(App):
    """Terminal dashboard for DevToolHub."""

    TITLE = "DevToolHub"

    CSS = """
    Screen {
        background: #1a1b26;
        color: #c0caf5;
    }
    DataTable {
        height: 1fr;
        background: #1a1b26;
    }
    DataTable > .datatable--header {
        background: #24283b;
        color: #7aa2f7;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: #364a82;
        color: #c0caf5;
    }
    #status-bar {
        height: 1;
        background: #24283b;
        color: #565f89;
        padding: 0 1;
    }
    Header {
        background: #7aa2f7;
        color: #1a1b26;
    }
    Footer {
        background: #24283b;
    }
    """

    BINDINGS = [
        Binding("k", "cursor_up", "Up", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("o", "open_tool", "Open"),
        Binding("s", "start_service", "Start"),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
    ]

    def action_cursor_up(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        table.action_cursor_up()

    def action_cursor_down(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        table.action_cursor_down()

    def __init__(self) -> None:
        super().__init__()
        self.hub_config = load_tools_config()
        self.checker = HealthChecker(hub_config=self.hub_config)

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="tools-table")
        yield Static("Ready", id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        table.add_columns("#", "Name", "Status", "Latency", "Details")
        table.cursor_type = "row"

        self._set_status("Checking services...")
        await self.checker.run_initial_check()
        self._refresh_table()

        self.set_interval(POLL_INTERVAL, self._poll_health)

    async def _poll_health(self) -> None:
        await self.checker._check_all()
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#tools-table", DataTable)
        table.clear()

        up_count = 0
        for i, tool in enumerate(self.hub_config.tools, 1):
            st = self.checker.statuses.get(tool.name, ToolStatus())

            if st.status == "up":
                status_text = Text("UP", style="bold #9ece6a")
                up_count += 1
            elif st.status == "down":
                status_text = Text("DOWN", style="bold #f7768e")
            else:
                status_text = Text("...", style="#565f89")

            latency = (
                Text(f"{st.latency_ms}ms", style="#c0caf5")
                if st.status == "up"
                else Text("--", style="dim")
            )

            details_parts = [f"{k}:{v}" for k, v in st.details.items()]
            details = Text(
                "  ".join(details_parts) if details_parts else "--",
                style="#565f89",
            )

            table.add_row(str(i), tool.name, status_text, latency, details, key=tool.name)

        total = len(self.hub_config.tools)
        self._set_status(f"{up_count}/{total} up | Polling every {POLL_INTERVAL}s")

    def _get_selected_tool(self) -> ToolConfig | None:
        table = self.query_one("#tools-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self.hub_config.tools):
            return self.hub_config.tools[table.cursor_row]
        return None

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-bar", Static).update(msg)

    def action_open_tool(self) -> None:
        """Smart open: focus window if window_title is set, otherwise open URL in browser."""
        tool = self._get_selected_tool()
        if not tool:
            return

        if tool.window_title:
            if focus_window(tool.window_title):
                self._set_status(f"Focused {tool.name}")
            elif tool.start_command:
                pid = launch_process(
                    tool.start_command, cwd=tool.start_cwd, wsl=tool.start_wsl
                )
                if pid:
                    self._set_status(f"Launched {tool.name} (PID {pid})")
                else:
                    self._set_status(f"Failed to launch {tool.name}")
            else:
                self._set_status(f"Window not found for {tool.name}")
        elif tool.url:
            webbrowser.open(tool.url)
            self._set_status(f"Opened {tool.name} in browser")
        else:
            self._set_status(f"No URL or window configured for {tool.name}")

    async def action_start_service(self) -> None:
        tool = self._get_selected_tool()
        if not tool or not tool.start_command:
            self._set_status("No start_command for selected tool")
            return

        st = self.checker.statuses.get(tool.name, ToolStatus())
        if st.status == "up":
            confirmed = await self.push_screen_wait(ConfirmStartScreen(tool.name))
            if not confirmed:
                self._set_status(f"Start cancelled for {tool.name}")
                return

        await self._do_start(tool)

    async def _do_start(self, tool: ToolConfig) -> None:
        pid = launch_process(
            tool.start_command, cwd=tool.start_cwd, wsl=tool.start_wsl
        )
        if pid:
            self._set_status(f"Started {tool.name} (PID {pid}) - checking...")
            await asyncio.sleep(3)
            await self.checker._check_all()
            self._refresh_table()
        else:
            self._set_status(f"Failed to start {tool.name}")

    async def action_refresh(self) -> None:
        self._set_status("Refreshing...")
        await self.checker._check_all()
        self._refresh_table()

    async def on_unmount(self) -> None:
        await self.checker.stop()


def run_tui() -> None:
    """Entry point for the TUI."""
    app = DevToolHubTUI()
    app.run()
