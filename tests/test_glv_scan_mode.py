"""Scan mode is per diagram, and defaults to telnet.

Per diagram and not global because comparing the two transports side by side
on the same relay is exactly what validating this feature needs. Telnet by
default because it is what people rely on today.
"""
from __future__ import annotations

import pytest

from pacct.web.glv.transport import (
    DEFAULT_PORTS,
    SCAN_MMS,
    SCAN_TELNET,
    pick_transport,
)


def test_default_ports_differ_so_the_pool_cannot_confuse_them():
    """LinkPool keys on ip:port; one relay watched both ways is two entries."""
    assert DEFAULT_PORTS[SCAN_TELNET] == 23
    assert DEFAULT_PORTS[SCAN_MMS] == 102


def test_pick_transport_returns_the_telnet_one_by_default():
    t = pick_transport(SCAN_TELNET, ip="192.0.2.10", port=23,
                       acc_password="OTTER", relay_model=None, logger=None)
    assert t.mode in ("target_region", "tar_digitals", "fast_meter_digitals")


def test_pick_transport_returns_the_mms_one_when_asked():
    # O ramo MMS importa `transport.mms`, que importa a py61850 -- a unica
    # dependencia do projeto que nao vem do PyPI (ver requirements.txt).
    pytest.importorskip("py61850")
    t = pick_transport(SCAN_MMS, ip="192.0.2.10", port=102,
                       acc_password="", relay_model=None, logger=None)
    assert t.mode == "mms"


def test_an_unknown_mode_falls_back_to_telnet_rather_than_raising():
    t = pick_transport("bogus", ip="192.0.2.10", port=23,
                       acc_password="OTTER", relay_model=None, logger=None)
    assert t.mode != "mms"
