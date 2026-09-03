"""PAC CT -- Protection, Automation & Control Commissioning Toolkit.

Dashboard, CLI and parsers for protection, automation and control systems:
SEL relays (411L, 487E, 751, 311C, ...) and IEC 61850 tools that serve any
vendor.

The version is read from the ``VERSION`` file at the project root, which is
also what ``pyproject.toml`` and the distribution bundle read -- one source,
so a release cannot disagree with itself.
"""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    for base in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        candidate = base / "VERSION"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return "0.0.0+unknown"


__version__ = _read_version()
