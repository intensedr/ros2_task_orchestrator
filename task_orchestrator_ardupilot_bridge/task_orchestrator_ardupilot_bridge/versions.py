"""Runtime dependency compatibility checks."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


BRIDGE_VERSION = "1.0.0"
LOCKED_PYMAVLINK_VERSION = "2.4.49"
MIN_PYMAVLINK_VERSION = (2, 4, 49)
MAX_PYMAVLINK_VERSION_EXCLUSIVE = (2, 5, 0)


def installed_pymavlink_version() -> str:
    try:
        return version("pymavlink")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "pymavlink is not installed; install requirements-ardupilot.txt"
        ) from exc


def require_supported_pymavlink() -> str:
    installed = installed_pymavlink_version()
    parsed = _numeric_prefix(installed)
    if not (MIN_PYMAVLINK_VERSION <= parsed < MAX_PYMAVLINK_VERSION_EXCLUSIVE):
        raise RuntimeError(
            "unsupported pymavlink version "
            f"{installed}; expected >=2.4.49,<2.5 (locked: {LOCKED_PYMAVLINK_VERSION})"
        )
    return installed


def _numeric_prefix(value: str) -> tuple[int, int, int]:
    parts: list[int] = []
    for raw_part in value.split("."):
        digits = "".join(character for character in raw_part if character.isdigit())
        parts.append(int(digits or 0))
        if len(parts) == 3:
            break
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]
