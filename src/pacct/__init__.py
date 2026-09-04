"""PAC CT -- Protection, Automation & Control Commissioning Toolkit.

Dashboard, CLI and parsers for protection, automation and control systems:
SEL relays (411L, 487E, 751, 311C, ...) and IEC 61850 tools that serve any
vendor.

The version is read from the ``VERSION`` file at the project root, which is
also what ``pyproject.toml`` and the distribution bundle read -- one source,
so a release cannot disagree with itself.
"""

from __future__ import annotations

from pacct.version import read_version

# The rules that give this string its meaning -- what a MAJOR means for a tool
# that writes into a relay, and why a snapshot is never offered as an update --
# are in `pacct/version.py`, next to the code that enforces them.
__version__ = read_version()


def _configure_selfiles() -> None:
    """Tell `selfiles` where this host keeps its overlay and its cache.

    Done at package import, once, because every entry point that reaches a
    registry goes through `import pacct.<something>` first, and a registry
    memoises the first answer it gets. Two answers to "where do relay models
    come from" is a bug, not a feature -- so the question is settled before
    anything can ask it.
    """
    try:
        import selfiles
    except ImportError:
        # The dependencies are not installed on this machine yet. That state
        # has to stay usable, because it is exactly when somebody needs
        # `--versao`, `--instalar` or `--atualizar`: an updater that cannot run
        # on a broken install cannot fix one. Nothing is silently
        # misconfigured by returning here -- if `selfiles` is absent then every
        # module that reads an SEL file fails on its own import, at the point
        # of use, loudly. There is no path where it imports but stays
        # unconfigured, which is the case this function exists to prevent.
        return

    from pacct import paths

    selfiles.configure(user_data_dir=paths.DATA_DIR,
                       cache_dir=paths.RDB_CACHE_DIR)


_configure_selfiles()
