"""Compatibility shims that must run before third-party imports.

Import and call these at the very top of any entry point that touches
`selprotopy`, ahead of the `import selprotopy` line itself.
"""

from __future__ import annotations

import sys


def ensure_telnetlib() -> None:
    """Make a bare ``import telnetlib`` work on Python 3.13+.

    ``telnetlib`` was removed from the standard library in Python 3.13. The
    vendored ``selprotopy`` still does a bare ``import telnetlib`` (and a
    ``from telnetlib import IAC, DO, ...``), and it monkeypatches
    ``telnetlib.Telnet.process_rawq`` so the SEL protocol's significant null
    bytes survive -- so it cannot simply be dropped.

    ``telnetlib3`` v4+ redistributes the removed module as a drop-in backport
    (verified: identical constant values, and ``Telnet.process_rawq`` present).
    Aliasing it into ``sys.modules`` under the original name satisfies every
    import form, which keeps ``selprotopy`` unmodified -- it is vendored, and
    patching it would be lost on the next resync.

    Idempotent, and a no-op on Python 3.12 and earlier.
    """
    if "telnetlib" in sys.modules:
        return

    try:
        import telnetlib  # noqa: F401  (Python 3.12 and earlier)
    except ImportError:
        pass
    else:
        return

    try:
        import telnetlib3.telnetlib as _backport
    except ImportError as exc:
        raise ImportError(
            "telnetlib foi removido no Python 3.13+ e o backport do "
            "'telnetlib3' nao foi encontrado. Instale as dependencias:\n"
            "    pip install -r requirements.txt"
        ) from exc

    sys.modules["telnetlib"] = _backport
