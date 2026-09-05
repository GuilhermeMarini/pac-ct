#!/usr/bin/env python3
"""Check the SET_D parser and the OLE writer against real RDBs.

Two things a unit test cannot cover, because the files are 40-140 MB and are
not in the repository:

    python3 tools/check_set_dnp_roundtrip.py
        Every SET_D in every RDB under rdbs/ and in the extraction cache:
        parse(b).serialize() == b.

    python3 tools/check_set_dnp_roundtrip.py --rebuild rdbs/obra.rdb
        Rebuild that RDB with no edits at all and compare every stream with
        the source. A writer that cannot reproduce a file byte for byte has
        no business writing a relay's settings.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cfbwrite  # noqa: E402
import olefile  # noqa: E402
from sellib import dnp_map as set_dnp  # noqa: E402

from pacct.paths import RDB_CACHE_DIR, RDBS_DIR  # noqa: E402


def check_roundtrip() -> int:
    checked = failed = 0
    for rdb_path in sorted(RDBS_DIR.glob("*.rdb")):
        ole = olefile.OleFileIO(str(rdb_path))
        try:
            for entry in ole.listdir(streams=True, storages=False):
                if not set_dnp._SETD_NAME_RE.match(entry[-1]):
                    continue
                data = ole.openstream(entry).read()
                checked += 1
                if set_dnp.parse(data).serialize() != data:
                    failed += 1
                    print(f"FALHOU {rdb_path.name}:{'/'.join(entry)}")
        finally:
            ole.close()

    for cache_entry in sorted(RDB_CACHE_DIR.glob("*/extracted")):
        for relay in set_dnp.discover(cache_entry):
            for session in relay.sessions:
                data = session.fs_path.read_bytes()
                checked += 1
                if set_dnp.parse(data).serialize() != data:
                    failed += 1
                    print(f"FALHOU {session.fs_path}")

    print(f"{checked} SET_D conferidos, {failed} falha(s)")
    if checked == 0:
        print(f"FALHOU: nenhum SET_D encontrado em {RDBS_DIR} nem em "
              f"{RDB_CACHE_DIR} -- nada foi conferido. Confira se há RDBs em "
              "rdbs/ ou entradas já extraídas no cache antes de confiar neste "
              "resultado.")
        return 1
    return 1 if failed else 0


def check_rebuild(src: Path) -> int:
    if not src.is_file():
        print(f"FALHOU {src.name}: arquivo não encontrado ({src})")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "rebuilt.rdb"
        try:
            cfbwrite.rebuild(src, dst, {})
        except cfbwrite.CfbWriteError as e:
            print(f"FALHOU {src.name}: {e}")
            return 1
        print(f"{src.name}: {src.stat().st_size} -> {dst.stat().st_size} bytes, "
              "todos os streams conferidos")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild", type=Path, default=None,
                    help="reconstrói este RDB sem edições e confere tudo")
    args = ap.parse_args()
    if args.rebuild:
        return check_rebuild(args.rebuild)
    return check_roundtrip()


if __name__ == "__main__":
    raise SystemExit(main())
