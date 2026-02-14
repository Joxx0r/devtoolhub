"""Configuration models for DevToolHub."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings


class ToolConfig(BaseModel):
    """Configuration for a single tool."""

    name: str
    url: str | None = None
    health_url: str | None = None
    health_check: Literal["http", "tcp", "process"] | None = None
    window_title: str | None = None
    process_pattern: str | None = None
    start_command: str | None = None
    start_cwd: str | None = None
    start_wsl: bool = False
    description: str = ""

    @model_validator(mode="after")
    def _expand_env_vars(self) -> ToolConfig:
        # Only expand Windows-side paths; skip WSL commands (bash handles $VAR)
        if not self.start_wsl and self.start_command:
            self.start_command = os.path.expandvars(self.start_command)
        if self.start_cwd:
            self.start_cwd = os.path.expandvars(self.start_cwd)
        return self

    def effective_health_check(self) -> str:
        """Determine which health check strategy to use."""
        if self.health_check:
            return self.health_check
        if self.health_url:
            return "http"
        if self.process_pattern:
            return "process"
        if self.url:
            return "http"
        return "none"


class HubConfig(BaseModel):
    """Top-level tools.yaml schema."""

    tools: list[ToolConfig] = []


class Settings(BaseSettings):
    """Application settings from environment."""

    port: int = 41001

    model_config = {"env_prefix": "DEVTOOLHUB_"}


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def load_tools_config(config_path: Path | None = None) -> HubConfig:
    """Load tools.yaml from the given path or auto-detect location."""
    if config_path is None:
        # Look relative to the package, then cwd
        candidates = [
            Path.cwd() / "tools.yaml",
            Path(__file__).resolve().parent.parent.parent / "tools.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = candidate
                break

    if config_path is None or not config_path.exists():
        return HubConfig()

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        return HubConfig()

    return HubConfig.model_validate(raw)
