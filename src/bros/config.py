"""Read `.bros.toml` from the repo root."""

import subprocess
import tomllib
from pathlib import Path


class ConfigError(SystemExit):
    pass


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if out.returncode:
        raise ConfigError("bros: not inside a git repository")
    return Path(out.stdout.strip())


def load(root: Path | None = None) -> dict:
    path = (root or repo_root()) / ".bros.toml"
    if not path.exists():
        raise ConfigError(f"bros: no {path.name} at {path.parent}; run /bros:setup")
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"bros: {path} is not valid TOML: {err}")


def require(config: dict, dotted: str):
    node = config
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(f"bros: .bros.toml is missing `{dotted}`")
        node = node[key]
    return node


def for_hook() -> tuple[dict, str]:
    """Hooks still run without config, but say so: a silent fallback hides a broken setup."""
    try:
        return load(), ""
    except SystemExit as err:
        return {}, f"{err} (using defaults until then)"
