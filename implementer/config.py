"""Configuration loading: credentials.json + config.json."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import selectors

TINDAKAN_RE = re.compile(r"^\d{2}\.\d{2}\.\d{3}$")


class ConfigError(Exception):
    """Raised when credentials/config are missing or malformed."""


@dataclass
class Credentials:
    base_url: str
    username: str
    password: str
    expected_user: str = ""

    @property
    def form_url(self) -> str:
        return self.base_url.rstrip("/") + selectors.FORM_PATH


@dataclass
class Settings:
    # What to look for in the CPPT table (step 5). Not hard-coded on purpose.
    cppt_keyword: str = "CPPT Alergi Imunologi"
    # Default tindakan codes applied to every patient without their own list.
    tindakan: list[str] = field(default_factory=list)
    # "3" == Selesai Dilakukan
    status: str = selectors.STATUS_SELESAI
    # Only touch CPPT rows whose "Tgl & Jam" falls on today.
    require_today: bool = True
    # Step 5: append a blank line to Subyektif and re-save the CPPT.
    touch_subyektif: bool = True
    # Don't re-record a tindakan that is already in Catatan Implementasi.
    skip_existing: bool = True
    # Browser behaviour
    headless: bool = False
    slow_mo_ms: int = 0
    timeout_ms: int = 30_000
    # Pause and wait for a keypress before every save (manual supervision).
    confirm_each_save: bool = False

    def validate(self) -> None:
        if not self.cppt_keyword.strip():
            raise ConfigError("cppt_keyword must not be empty")
        if self.status not in selectors.STATUS_LABELS:
            raise ConfigError(
                f"status must be one of {sorted(selectors.STATUS_LABELS)}, got {self.status!r}"
            )
        for code in self.tindakan:
            validate_tindakan(code)


def validate_tindakan(code: str) -> str:
    """Tindakan codes are always xx.xx.xxx, digits only."""
    code = code.strip()
    if not TINDAKAN_RE.match(code):
        raise ConfigError(
            f"invalid tindakan code {code!r}: expected the format xx.xx.xxx "
            "with digits only (e.g. 01.01.010)"
        )
    return code


def _read_json(path: Path, what: str) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"{what} file not found: {path}")
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def load_credentials(path: Path) -> Credentials:
    data = _read_json(path, "credentials")
    missing = [k for k in ("base_url", "username", "password") if not data.get(k)]
    if missing:
        raise ConfigError(f"{path} is missing required key(s): {', '.join(missing)}")

    password = str(data["password"])
    if password.startswith("env:"):
        var = password[4:]
        resolved = os.environ.get(var)
        if not resolved:
            raise ConfigError(f"password refers to env var {var!r}, which is not set")
        password = resolved

    creds = Credentials(
        base_url=str(data["base_url"]).strip(),
        username=str(data["username"]).strip(),
        password=password,
        expected_user=str(data.get("expected_user", "")).strip(),
    )
    _warn_if_world_readable(path)
    return creds


def load_settings(path: Path | None) -> Settings:
    settings = Settings()
    if path is None:
        return settings
    data = _read_json(path, "config")
    known = {f for f in Settings.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"{path} has unknown key(s): {', '.join(sorted(unknown))}. "
            f"Known keys: {', '.join(sorted(known))}"
        )
    for key, value in data.items():
        if key == "tindakan":
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                raise ConfigError("config 'tindakan' must be a string or a list of strings")
            value = [validate_tindakan(str(v)) for v in value]
        setattr(settings, key, value)
    return settings


def _warn_if_world_readable(path: Path) -> None:
    """POSIX only: nudge the user if the secrets file is readable by others."""
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        print(
            f"warning: {path} is readable by other users. "
            f"Run: chmod 600 {path}",
            file=sys.stderr,
        )
