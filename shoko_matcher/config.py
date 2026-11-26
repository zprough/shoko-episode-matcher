"""Configuration loading from CLI args, environment variables and a config file.

Resolution order (later wins): a JSON config file, then environment variables,
then explicit CLI arguments. The API key may also be supplied via a file with a
single line containing the key.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Optional


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


ENV_URL = "SHOKO_URL"
ENV_APIKEY = "SHOKO_APIKEY"
ENV_CONFIG = "SHOKO_MATCHER_CONFIG"

# A key file shipped/existing next to the project, convenient for local use.
DEFAULT_KEY_FILE = "file-matcher-api.txt"
DEFAULT_CONFIG_FILE = "shoko-matcher.config.json"


@dataclass
class Config:
    base_url: str = "http://localhost:8111"
    apikey: str = ""
    dry_run: bool = True
    min_confidence: float = 80.0
    series_filter: Optional[str] = None
    import_folder: Optional[int] = None
    limit: Optional[int] = None
    report_path: Optional[str] = None
    create_missing_series: bool = False
    episode_id_type: str = "shoko"  # shoko | anidb
    link_method: str = "episode"  # episode | range
    absolute_numbering: bool = False
    timeout: float = 30.0
    max_retries: int = 4
    page_size: int = 1000

    def normalized_base_url(self) -> str:
        url = self.base_url.strip().rstrip("/")
        if not url:
            raise ConfigError("base_url is empty")
        if not url.endswith("/api/v3"):
            url = f"{url}/api/v3"
        return url + "/"


def _read_key_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"Invalid JSON in config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object")
    return data


def _coerce(name: str, value: Any) -> Any:
    """Coerce a raw config/env value to the expected type for a known field."""
    if value is None:
        return None
    if name in {"limit", "import_folder", "max_retries", "page_size"}:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if name in {"min_confidence", "timeout"}:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if name in {"dry_run", "create_missing_series", "absolute_numbering"}:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    return value


def load_config(argv: Any) -> Config:
    """Build a :class:`Config` from parsed argparse namespace ``argv``."""
    values: dict[str, Any] = {}

    # 1) Config file
    config_path_env = os.environ.get(ENV_CONFIG)
    if config_path_env:
        values.update(_read_config_file(Path(config_path_env)))
    elif Path(DEFAULT_CONFIG_FILE).is_file():
        values.update(_read_config_file(Path(DEFAULT_CONFIG_FILE)))

    # 2) Environment variables
    if os.environ.get(ENV_URL):
        values["base_url"] = os.environ[ENV_URL]
    if os.environ.get(ENV_APIKEY):
        values["apikey"] = os.environ[ENV_APIKEY]

    # 3) CLI arguments (only when explicitly set)
    cli_map = {
        "base_url": "base_url",
        "apikey": "apikey",
        "min_confidence": "min_confidence",
        "series": "series_filter",
        "import_folder": "import_folder",
        "limit": "limit",
        "report": "report_path",
        "episode_id_type": "episode_id_type",
        "link_method": "link_method",
        "timeout": "timeout",
        "max_retries": "max_retries",
    }
    for arg_name, field_name in cli_map.items():
        raw = getattr(argv, arg_name, None)
        if raw is not None:
            values[field_name] = raw

    if getattr(argv, "apply", False):
        values["dry_run"] = False
    if getattr(argv, "dry_run", False):
        values["dry_run"] = True
    if getattr(argv, "create_missing_series", False):
        values["create_missing_series"] = True
    if getattr(argv, "absolute_numbering", False):
        values["absolute_numbering"] = True

    # API key from file if still missing
    apikey_file = getattr(argv, "apikey_file", None)
    if not values.get("apikey"):
        if apikey_file:
            values["apikey"] = _read_key_file(Path(apikey_file))
        elif Path(DEFAULT_KEY_FILE).is_file():
            values["apikey"] = _read_key_file(Path(DEFAULT_KEY_FILE))

    # Coerce known numeric/boolean fields coming from JSON/env
    for key in list(values):
        values[key] = _coerce(key, values[key])

    known = {f.name for f in fields(Config)}
    clean = {k: v for k, v in values.items() if k in known and v is not None}
    config = Config(**clean)

    if not config.apikey:
        raise ConfigError(
            "Missing API key. Set --apikey, SHOKO_APIKEY, an apikey file, or a "
            "config file."
        )
    if config.episode_id_type not in {"shoko", "anidb"}:
        raise ConfigError("--episode-id-type must be 'shoko' or 'anidb'")
    if config.link_method not in {"episode", "range"}:
        raise ConfigError("--link-method must be 'episode' or 'range'")
    return config
