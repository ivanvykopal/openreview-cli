"""Configuration loading and credential resolution.

The tool reads an optional YAML file (default ``~/.openreview_cli.yaml``)
holding the settings below; unknown keys are ignored so future versions
of the file keep working with older code::

    username: alice@example.org
    password: secret
    venues:
      - aclweb.org/ACL/ARR/2026/August
    download_dir: ~/openreview-downloads
    db_path: ~/.openreview_cli.sqlite3
    request_delay: 0.5

Credentials are resolved lazily at client construction time, in this
order: CLI flags > environment variables (``OPENREVIEW_USERNAME`` /
``OPENREVIEW_PASSWORD``) > config file > interactive prompt.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .exceptions import AuthenticationError, ConfigError

#: Environment variable holding the OpenReview username.
ENV_USERNAME = "OPENREVIEW_USERNAME"

#: Environment variable holding the OpenReview password.
ENV_PASSWORD = "OPENREVIEW_PASSWORD"

#: Default location of the YAML config file.
CONFIG_PATH = Path("~/.openreview_cli.yaml").expanduser()


@dataclass
class Config:
    """Runtime configuration for the OpenReview CLI.

    Attributes:
        username: Optional username from the config file.
        password: Optional password from the config file.
        venues: List of venue group ids to monitor.
        download_dir: Base directory for downloaded files.
        db_path: Path of the local SQLite state database.
        request_delay: Minimum delay in seconds between API calls.
    """

    username: str | None = None
    password: str | None = None
    venues: list[str] = field(default_factory=list)
    download_dir: str = "~/openreview-downloads"
    db_path: str = "~/.openreview_cli.sqlite3"
    request_delay: float = 0.5


def expand_path(p: str) -> Path:
    """Expand ``~`` in a path string and return it as a :class:`Path`.

    Args:
        p: A path string, possibly starting with ``~``.

    Returns:
        The expanded path.
    """
    return Path(p).expanduser()


def _coerce_config(data: dict[str, Any]) -> Config:
    """Build a :class:`Config` from raw YAML data, ignoring unknown keys."""
    config = Config()

    if "username" in data:
        config.username = str(data["username"]) if data["username"] is not None else None
    if "password" in data:
        config.password = str(data["password"]) if data["password"] is not None else None
    if "venues" in data and data["venues"] is not None:
        config.venues = [str(v) for v in data["venues"]]
    if "download_dir" in data and data["download_dir"] is not None:
        config.download_dir = str(data["download_dir"])
    if "db_path" in data and data["db_path"] is not None:
        config.db_path = str(data["db_path"])
    if "request_delay" in data and data["request_delay"] is not None:
        try:
            config.request_delay = float(data["request_delay"])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid request_delay: {data['request_delay']!r}") from exc

    return config


def load_config(path: Path | None = None) -> Config:
    """Load configuration from a YAML file.

    Args:
        path: Config file location; defaults to :data:`CONFIG_PATH`
            (``~/.openreview_cli.yaml``).

    Returns:
        The parsed configuration. Unknown keys are ignored; a missing
        file yields an all-defaults :class:`Config`.

    Raises:
        ConfigError: If the file exists but is not valid YAML or is not
            a mapping at the top level.
    """
    config_path = Path(path).expanduser() if path is not None else CONFIG_PATH

    if not config_path.exists():
        return Config()

    try:
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse config file {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc

    if data is None:
        return Config()

    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {config_path} must contain a YAML mapping, "
            f"got {type(data).__name__}"
        )

    return _coerce_config(data)


def resolve_credentials(
    config: Config,
    cli_username: str | None,
    cli_password: str | None,
    allow_prompt: bool = True,
) -> tuple[str, str]:
    """Resolve a full (username, password) pair.

    Precedence (per component, independently): CLI flag > environment
    variable > config file > interactive prompt. Passwords are read with
    :func:`getpass.getpass` so they are not echoed.

    Args:
        config: Loaded configuration (config-file credentials).
        cli_username: Username given on the command line, if any.
        cli_password: Password given on the command line, if any.
        allow_prompt: Whether interactive prompting is permitted.
            When ``False`` and credentials are incomplete an
            :class:`AuthenticationError` is raised instead.

    Returns:
        A ``(username, password)`` tuple, both non-empty.

    Raises:
        AuthenticationError: When credentials remain incomplete and
            prompting is disabled (or fails).
    """
    username = (
        cli_username
        or os.environ.get(ENV_USERNAME)
        or config.username
    )
    password = (
        cli_password
        or os.environ.get(ENV_PASSWORD)
        or config.password
    )

    if not username or not password:
        if not allow_prompt:
            missing = "username" if not username else "password"
            raise AuthenticationError(
                f"Missing OpenReview credentials: no {missing} provided via "
                "CLI flag, environment variable, or config file"
            )
        try:
            if not username:
                username = input("OpenReview username: ").strip()
            if not password:
                password = getpass.getpass("OpenReview password: ")
        except (EOFError, KeyboardInterrupt) as exc:
            raise AuthenticationError("Credential prompt interrupted") from exc

    if not username or not password:
        raise AuthenticationError(
            "OpenReview credentials incomplete after prompting"
        )

    return username, password
