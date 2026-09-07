"""The two flags the Windows launcher's menu drives.

The launcher is a `.cmd` the suite cannot run, so everything it can get wrong
lives here instead: it is a menu and six calls into `app.py`, and these pin
what those calls mean.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _app_module():
    """Import `app.py` without running it (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("_app_launcher_flags",
                                                  ROOT / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_app_launcher_flags"] = mod
    spec.loader.exec_module(mod)
    return mod


def _parse(argv: list[str]):
    app = _app_module()
    parser = app.build_parser()
    return parser.parse_known_args(argv)[0]


def test_the_menu_can_check_without_updating():
    """"Verificar atualizacoes" and "Atualizar" are two menu items because
    they are two decisions. The check downloads nothing."""
    args = _parse(["--verificar"])
    assert args.verificar is True
    assert args.atualizar is False


def test_the_menu_can_install_somewhere_else():
    args = _parse(["--instalar-em", r"C:\Users\eu\AppData\Local\PAC-CT"])
    assert args.instalar_em == r"C:\Users\eu\AppData\Local\PAC-CT"


def test_checking_for_updates_never_pays_the_dependency_bootstrap():
    """`main()` builds the venv and installs every wheel BEFORE it dispatches a
    flag. Auto-check runs on every launcher start, so paying that would put
    seconds between the double click and the menu -- which is the cost the
    whole "never on the boot path" rule exists to avoid.

    It costs nothing to skip: `update.py` deliberately names no runtime
    dependency (pinned in test_update.py), so the check needs Python and
    `urllib` and nothing else.
    """
    import inspect

    app = _app_module()
    src = inspect.getsource(app.main)
    check = src.index("args.verificar")
    install = src.index("install_requirements(allow_break=use_break_system,")
    assert check < install, (
        "--verificar must be answered before install_requirements runs")


def test_the_launcher_ships_in_the_bundle():
    """The menu is only reachable if the `.cmd` travels with the program."""
    assert (ROOT / "tools" / "dist" / "pac-ct.cmd").is_file()


# -- the launcher itself, which the suite cannot run -------------------------

def _cmd() -> str:
    return (ROOT / "tools" / "dist" / "pac-ct.cmd").read_text(encoding="utf-8")


def test_arguments_still_go_straight_through():
    """`pac-ct.cmd --port 9000` and `pac-ct.cmd --atualizar` are in
    INSTALAR.txt and in people's notes. The menu is what happens with NO
    arguments; with them the file does what it always did."""
    cmd = _cmd()
    assert 'if not "%~1"=="" (' in cmd
    assert '"%PY%" "%VERSION_DIR%\\app.py" --web %*' in cmd


def test_the_menu_calls_the_flags_it_promises():
    cmd = _cmd()
    for flag in ("--web", "--versao", "--instalar-em", "--verificar",
                 "--atualizar"):
        assert flag in cmd, f"the menu offers it but never calls {flag}"


def test_the_auto_check_reads_the_exit_code_and_not_the_text():
    """`run_check` answers 10 for "ha versao nova" so the launcher can mark
    the menu without parsing Portuguese out of stdout."""
    assert "EQU 10" in _cmd()


def test_launcher_settings_sit_with_the_users_data():
    """`userdata\\` on a versioned install and the folder itself on a portable
    one -- the launcher already computes that split, and both survive their
    own update path (`userdata` is never touched; a portable update moves only
    the paths it can name, and this is not one)."""
    cmd = _cmd()
    assert 'set "CFG=%DATA_DIR%\\launcher.cfg"' in cmd
    assert "auto_check" in cmd


def test_every_jump_has_somewhere_to_land():
    """A `goto` with no label is a batch file that silently falls off the end,
    and there is no compiler here to catch it."""
    import re

    cmd = _cmd()
    labels = set(re.findall(r"^:([a-z_]+)", cmd, re.MULTILINE))
    jumps = set(re.findall(r"goto ([a-z_]+)", cmd))
    assert jumps <= labels, f"goto with no label: {sorted(jumps - labels)}"
