"""Health checking logic shared by web and TUI interfaces."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from devtoolhub.config import HubConfig, ToolConfig

logger = logging.getLogger("devtoolhub")

POLL_INTERVAL = 10  # seconds
PROBE_TIMEOUT = 3  # seconds


@dataclass
class ToolStatus:
    status: str = "unknown"  # "up", "down", "unknown"
    latency_ms: int = 0
    last_checked: datetime | None = None
    details: dict[str, str] = field(default_factory=dict)


@dataclass
class HealthChecker:
    """Background health checker for all configured tools."""

    hub_config: HubConfig
    statuses: dict[str, ToolStatus] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = None
    _http_client: httpx.AsyncClient | None = None

    def _init_statuses(self) -> None:
        for tool in self.hub_config.tools:
            self.statuses[tool.name] = ToolStatus()

    async def run_initial_check(self) -> None:
        """Run one health check synchronously so first page load has data."""
        self._init_statuses()
        self._http_client = httpx.AsyncClient(timeout=PROBE_TIMEOUT)
        await self._check_all()

    def start_polling(self) -> None:
        """Start the background polling loop (call after run_initial_check)."""
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
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            await self._check_all()

    async def _check_all(self) -> None:
        tasks = [self._check_tool(tool) for tool in self.hub_config.tools]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_tool(self, tool: ToolConfig) -> None:
        strategy = tool.effective_health_check()
        t0 = time.monotonic()
        up = False
        details: dict[str, str] = {}
        try:
            if strategy == "http":
                up, details = await self._check_http(tool)
            elif strategy == "tcp":
                up = await self._check_tcp(tool)
                if up:
                    parsed = urlparse(tool.url)
                    details["endpoint"] = f"{parsed.hostname}:{parsed.port}"
            elif strategy == "process":
                up, details = await self._check_process(tool)
            else:
                return
        except Exception:
            up = False

        latency = int((time.monotonic() - t0) * 1000)
        self.statuses[tool.name] = ToolStatus(
            status="up" if up else "down",
            latency_ms=latency,
            last_checked=datetime.now(timezone.utc),
            details=details,
        )

    async def _check_http(self, tool: ToolConfig) -> tuple[bool, dict[str, str]]:
        url = tool.health_url or tool.url
        if not url:
            return False, {}
        assert self._http_client is not None
        resp = await self._http_client.get(url)
        up = resp.status_code < 400
        details: dict[str, str] = {}

        parsed = urlparse(url)
        details["port"] = str(parsed.port or 80)

        # Try to extract useful info from JSON health responses
        if up:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    details.update(self._extract_health_info(data))
            except Exception:
                pass

        return up, details

    def _extract_health_info(self, data: dict[str, Any]) -> dict[str, str]:
        """Pull interesting fields from a JSON health response."""
        info: dict[str, str] = {}

        # Memory: "memory" (bytes) or "memoryMB" (already MB)
        for mem_key in ("memoryMB", "memory"):
            mem = data.get(mem_key)
            if isinstance(mem, dict):
                rss = mem.get("rss")
                if rss and isinstance(rss, (int, float)):
                    if mem_key == "memoryMB":
                        info["memory"] = f"{int(rss)} MB"
                    else:
                        info["memory"] = f"{rss / (1024 * 1024):.0f} MB"
                break

        # Version
        for key in ("version", "ver"):
            if key in data and data[key]:
                info["version"] = str(data[key])
                break

        # Uptime: "uptime" or "uptimeSeconds"
        for key in ("uptimeSeconds", "uptime"):
            val = data.get(key)
            if isinstance(val, (int, float)):
                secs = int(val)
                hours = secs // 3600
                mins = (secs % 3600) // 60
                if hours > 0:
                    info["uptime"] = f"{hours}h {mins}m"
                else:
                    info["uptime"] = f"{mins}m"
                break

        # Nested index stats (e.g. memoryIndex.types)
        mem_idx = data.get("memoryIndex")
        if isinstance(mem_idx, dict):
            for key in ("types", "files", "members"):
                val = mem_idx.get(key)
                if isinstance(val, (int, float)):
                    info[key] = f"{int(val):,}"

        # Top-level stats
        for key in ("types", "files", "members"):
            if key not in info and key in data and isinstance(data[key], (int, float)):
                info[key] = f"{int(data[key]):,}"

        return info

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

    async def _check_process(
        self, tool: ToolConfig
    ) -> tuple[bool, dict[str, str]]:
        pattern = tool.process_pattern
        if not pattern:
            return False, {}
        try:
            proc = await asyncio.create_subprocess_exec(
                "wmic",
                "process",
                "where",
                f"commandline like '%{pattern}%'",
                "get",
                "processid,workingsetsize",
                "/format:csv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=PROBE_TIMEOUT + 2
            )
            output = stdout.decode("utf-8", errors="replace")
            details: dict[str, str] = {}
            # Parse CSV: Node,ProcessId,WorkingSetSize
            lines = [
                ln.strip()
                for ln in output.strip().splitlines()
                if ln.strip() and not ln.strip().startswith("Node")
            ]
            if not lines:
                return False, {}

            # Aggregate memory across matching processes
            total_mem = 0
            pids: list[str] = []
            for line in lines:
                parts = line.split(",")
                if len(parts) >= 3:
                    pid = parts[1].strip()
                    ws = parts[2].strip()
                    if pid and pid.isdigit():
                        pids.append(pid)
                    if ws and ws.isdigit():
                        total_mem += int(ws)

            if pids:
                details["pid"] = pids[0] if len(pids) == 1 else f"{pids[0]} (+{len(pids)-1})"
            if total_mem > 0:
                details["memory"] = f"{total_mem / (1024 * 1024):.0f} MB"

            return len(pids) > 0, details
        except (OSError, asyncio.TimeoutError):
            return False, {}

    def get_all_statuses(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for name, st in self.statuses.items():
            result[name] = {
                "status": st.status,
                "latency_ms": st.latency_ms,
                "details": st.details,
                "last_checked": (
                    st.last_checked.isoformat() if st.last_checked else None
                ),
            }
        return result
