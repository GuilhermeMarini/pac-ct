"""A minimal SET_D map, for the tests of the DNP map EDITOR.

The parser and its round-trip contract live in `selfiles.dnp_map` and are
tested there. What is tested here is the editor built on top of it -- the
per-session diffs, the export, the cross-relay copy -- and those need a file
to edit. This is that file, kept small and local rather than reached for
across a package boundary.
"""

from __future__ import annotations

SAMPLE_411L = (
    b"[INFO]\r\n"
    b"RELAYTYPE=SEL-411L-A\r\n"
    b"FID=SEL-411L-A-RXXX-VX-Z022004-DXXXXXXXX\r\n"
    b"BFID=SLBT-4XX-R300-V0-Z001002-D20200229\r\n"
    b"PARTNO=0411LAX6X5C7DDXH5D474XX\r\n"
    b"[D1]\r\n"
    b'MINDIST,"1.0"\x1c\r\n'
    b'MAXDIST,"10000.0"\x1c\r\n'
    b'BI_1,"PSV22"\x1c\r\n'
    b'BI_2,""\x1c\r\n'
    b'AI_1,"IAMAG"\x1c\r\n'
    b'AI_SCA1,"1.0"\x1c\r\n'
    b'AI_DBD1,"0.5"\x1c\r\n'
    b'CO_1,""\x1c\r\n'
    b'CO_DBD1,""\x1c\r\n'
)
