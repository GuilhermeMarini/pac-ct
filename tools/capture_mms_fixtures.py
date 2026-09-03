#!/usr/bin/env python3
"""Record what the MMS map layer needs, so its tests run with no relay.

    python3 tools/capture_mms_fixtures.py 203.0.113.61

Run this once against a relay whose model has a shipped table. The recorded
directory listing is what proves the FC-from-the-relay trick works, and the
data definitions plus one read are what prove the positional decode aligns.

Every fixture this writes carries a top-level ``provenance`` field naming the
live relay and its FID -- so a fixture that was never actually captured
cannot be mistaken for one that was. See ``tools/synth_mms_fixtures.py`` for
the stand-in this tool is meant to replace.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from py61850 import MmsClient, decode_data_definition  # noqa: E402
from py61850.mms import pdu  # noqa: E402

from pacct.paths import PROJECT_ROOT  # noqa: E402

OUT = PROJECT_ROOT / "tests" / "fixtures" / "mms"
CONTAINERS = ["PLT1GGIO1$ST", "ALT1GGIO1$ST", "VB1XGGIO1$ST",
              "IN1XGGIO1$ST", "OUT1GGIO1$ST"]


def _write(path: Path, provenance: str, key: str, payload) -> None:
    path.write_text(json.dumps({"provenance": provenance, key: payload}, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=102)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    c = MmsClient(args.host, port=args.port, timeout=10)
    c.connect()
    try:
        lds = c.get_server_directory()
        ann = [line for line in lds if line.endswith("ANN")][0]

        fid_raw = c.read_value(ann, "LLN0$DC$NamPlt$swRev")
        fid = str(fid_raw or "").removeprefix("FID=") or "FID desconhecido"
        provenance = f"relay {args.host} {fid}"

        directory = sorted(c.get_logical_device_directory(ann))
        _write(OUT / "451_ann_directory.json", provenance, "directory", directory)

        defs, reads, expected = {}, {}, {}
        for item in CONTAINERS:
            defs[item] = decode_data_definition(c.get_data_definition(ann, item))
            raw = c.read(ann, item)
            reads[item] = base64.b64encode(raw).decode()
            values = pdu.decode_read_response(raw)[0]
            children = defs[item]["type"]["structure"]
            expected[item] = {}
            for child, value in zip(children, values, strict=True):
                sub = child["type"]
                names = ([g["name"] for g in sub["structure"]]
                         if isinstance(sub, dict) and "structure" in sub else [])
                if "stVal" in names:
                    expected[item][child["name"]] = value[names.index("stVal")]

        _write(OUT / "451_datadefs.json", provenance, "datadefs", defs)
        _write(OUT / "451_reads_b64.json", provenance, "reads", reads)
        _write(OUT / "451_expected_stvals.json", provenance, "expected", expected)
        print(f"gravado em {OUT}, LDs: {lds}, provenance: {provenance}")
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
