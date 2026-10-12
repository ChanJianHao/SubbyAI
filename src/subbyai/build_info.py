"""Immutable build identity embedded by the packager, shared by every UI surface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module

from .branding import VERSION


@dataclass(frozen=True)
class BuildInfo:
    version: str
    built_at: datetime | None = None
    commit: str = ""
    source_dirty: bool = True

    @property
    def display_text(self) -> str:
        if self.built_at is None:
            return f"v{self.version} · Build date unavailable"
        local = self.built_at.astimezone()
        offset = local.strftime("%z")
        zone = f"UTC{offset[:3]}:{offset[3:]}"
        return f"v{self.version} · Built {local:%Y-%m-%d %H:%M:%S} {zone}"

    @property
    def tooltip(self) -> str:
        if self.built_at is None:
            return "The build date is embedded when SubbyAI is packaged."
        return (
            f"SubbyAI {self.version}\n"
            f"Build timestamp: {self.built_at.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC\n"
            "This identifies the installed build and stays the same when you restart."
            + (f"\nSource commit: {self.commit}" if self.commit else "")
            + (" (uncommitted changes)" if self.commit and self.source_dirty else "")
        )


def get_build_info() -> BuildInfo:
    """Source checkouts have no build date; never substitute the current launch time."""
    try:
        metadata = import_module("subbyai_build_info")
        built_at = datetime.fromisoformat(metadata.BUILT_AT_UTC)
        if metadata.VERSION != VERSION or built_at.utcoffset() is None:
            return BuildInfo(VERSION)
    except (ImportError, AttributeError, TypeError, ValueError):
        return BuildInfo(VERSION)
    commit = getattr(metadata, "COMMIT", "")
    if not isinstance(commit, str) or not re.fullmatch(r"[a-f0-9]{40}", commit):
        commit = ""
    return BuildInfo(
        VERSION, built_at.astimezone(UTC), commit,
        getattr(metadata, "SOURCE_DIRTY", True) is not False,
    )
