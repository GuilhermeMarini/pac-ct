#!/usr/bin/env python3
"""Which variables DRAWN in the GLE logic have no IEC 61850 address?

Writes `fixtures/gle_sem_61850.txt`.

    python3 tools/gle_vs_61850.py
    python3 tools/gle_vs_61850.py --rdb "rdbs/SE X.rdb" --scd "rdbs/SE X.scd"

The sibling `relay_word_vs_61850.py` asks the same question of a relay's WHOLE
Relay Word. This one asks it of the subset the diagrams actually reference,
which is the number that decides what the GLV shows: a bit nobody drew cannot
go grey on a page.

Matching is per relay, not per model -- each relay is paired with its own IED in
the SCD -- and only the report groups by model afterwards, because within one
model the relays draw nearly the same logic.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _sel61850 as maps  # noqa: E402
from pacct.parsers import rdb  # noqa: E402
from pacct.parsers.gle import parse_gle  # noqa: E402
from pacct.paths import FIXTURES_DIR, RDB_CACHE_DIR, RDBS_DIR  # noqa: E402
from pacct.web.glv.gle_pages import collect_bits_per_page  # noqa: E402

ICD_MAP = FIXTURES_DIR / "ICD files" / "SEL" / "wordbits.json"
OUT = FIXTURES_DIR / "gle_sem_61850.txt"

# A Relay Word name as the GLE spells it. Leading digits are the rule, not the
# exception -- `50G1P`, `27TC1`, `3I2` -- so anchoring on a letter silently drops
# every ANSI element. Pure digits are drawn constants ("0", "1"), not names.
NAME = re.compile(r"^[A-Z0-9_]+$")


def newest(directory: Path, suffix: str) -> Path | None:
    found = sorted(directory.glob(f"*{suffix}"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def extracted_dir(rdb_path: Path) -> Path:
    """The RDB's extraction, reusing the shared content cache when it is there.

    `cache/rdb/<sha256>/` is keyed by content, so an RDB already opened in the
    web tool costs nothing here.
    """
    sha = rdb.sha256_file(rdb_path)
    cached = RDB_CACHE_DIR / sha / "extracted"
    if cached.is_dir():
        return cached
    size = rdb_path.stat().st_size
    with rdb_path.open("rb") as fh:
        info = rdb.process_upload_stream(fh, size, rdb_path.name)
    return info.extract_dir


def gle_bits(relay) -> tuple[set, list]:
    """Union of the bits every GLE of this relay draws, plus any parse failure."""
    bits, failed = set(), []
    for g in relay.gles:
        try:
            for page_bits in collect_bits_per_page(parse_gle(g.fs_path)).values():
                bits |= {b.upper() for b in page_bits}
        except Exception as e:                       # noqa: BLE001 - report, don't stop
            failed.append(f"{g.name}: {type(e).__name__}: {e}")
    return {b for b in bits if NAME.match(b) and not b.isdigit()}, failed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rdb", type=Path, default=None,
                    help="RDB do projeto (padrao: o .rdb mais recente em rdbs/)")
    ap.add_argument("--scd", type=Path, default=None,
                    help="SCD do projeto (padrao: o .scd mais recente em rdbs/)")
    ap.add_argument("--icd", type=Path, default=ICD_MAP)
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    args = ap.parse_args()

    rdb_path = args.rdb or newest(RDBS_DIR, ".rdb")
    scd_path = args.scd or newest(RDBS_DIR, ".scd")
    if rdb_path is None:
        print("nenhum .rdb em rdbs/ -- passe --rdb", file=sys.stderr)
        return 2
    if scd_path is None:
        print("nenhum .scd em rdbs/ -- passe --scd", file=sys.stderr)
        return 2

    scd = maps.load_scd(scd_path)
    icd, _groups = maps.load_icd(args.icd)
    relays = rdb._scan_existing(extracted_dir(rdb_path))

    per_model: dict = collections.defaultdict(lambda: collections.defaultdict(set))
    rows, empty_models, warnings = [], {}, []
    for r in relays:
        names, failed = gle_bits(r)
        warnings.extend(f"{r.name}/{f}" for f in failed)
        key = maps.norm_part(r.name)
        s_bits = scd["bits"].get(key, set())
        part = scd["part"].get(key) or maps.norm_part(r.model)
        i_bits = icd.get(part, set())
        missing = names - (s_bits | i_bits)
        model = r.model or "?"
        for b in missing:
            per_model[model][b].add(r.name)
        if not names:
            empty_models.setdefault(model, []).append(r.name)
        rows.append((model, r.name, len(names), len(s_bits), len(i_bits),
                     len(missing), bool(s_bits)))

    L: list = []
    A = L.append
    A("=" * 74)
    A("VARIAVEIS DO GLE SEM ENDERECO IEC 61850")
    A("=" * 74)
    A("")
    A(f"Gerado em {datetime.date.today().isoformat()} por "
      f"tools/{Path(__file__).name}.")
    A(f"RDB: {rdb_path.name}")
    A(f"SCD: {scd_path.name}")
    A(f"ICD: {args.icd.name}")
    A("")
    A("METODO")
    A("  Pra cada rele: uniao dos bits que os GLE dele desenham, menos a uniao")
    A("  de (sAddr do proprio IED no SCD) com (mapa ICD da peca). O que sobra")
    A("  esta na logica e nao tem endereco MMS -- em modo MMS o GLV mostra esse")
    A("  bit como indeterminado.")
    A("")
    A("  O casamento e' POR RELE (cada um com o seu IED). O agrupamento por")
    A("  modelo abaixo e' so do relatorio: dentro de um modelo os reles desenham")
    A("  quase a mesma logica, entao a lista e' a UNIAO e cada nome vale pra pelo")
    A("  menos um rele daquele modelo.")
    A("")
    if warnings:
        A("  GLE que nao abriram:")
        for w in warnings:
            A(f"    {w}")
        A("")
    A("POR RELE")
    A(f"  {'modelo':9s} {'rele':17s} {'GLE':>5s} {'SCD':>6s} {'ICD':>6s} "
      f"{'FALTA':>6s} {'cob':>5s}  SCD?")
    for model, name, nb, ns, ni, nm, has_s in rows:
        cov = f"{(nb - nm) / nb:.0%}" if nb else "--"
        A(f"  {model:9s} {name:17s} {nb:5d} {ns:6d} {ni:6d} {nm:6d} {cov:>5s}  "
          f"{'sim' if has_s else 'NAO (so ICD)'}")
    A("")

    for model in sorted(per_model, key=lambda m: -len(per_model[m])):
        bits = per_model[model]
        nrel = len(set().union(*bits.values())) if bits else 0
        A("=" * 74)
        A(f"{model}  --  {len(bits)} variaveis sem endereco 61850  ({nrel} rele(s))")
        A("=" * 74)
        for fam, names in maps.by_family(bits):
            A(f"  -- {fam}  ({len(names)})")
            L.extend(maps.columns(names))
            A("")
        per_relay: dict = collections.defaultdict(list)
        for b, rs in bits.items():
            for r in rs:
                per_relay[r].append(b)
        A("  por rele:")
        for r in sorted(per_relay):
            A(f"     {r:20s} {len(per_relay[r])}")
        A("")

    for model, names in sorted(empty_models.items()):
        if model in per_model:
            continue
        A("=" * 74)
        A(f"{model}  --  nenhuma variavel faltando")
        A("=" * 74)
        A(f"  {', '.join(names)}: o GLE desses reles e' um template vazio (uma")
        A("  pagina sem elementos). Nao ha logica desenhada -- nada a comparar.")
        A("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{args.out}: {args.out.stat().st_size / 1024:.1f} KB, "
          f"{len(relays)} rele(s)")
    for model in sorted(per_model):
        print(f"  {model:9s} {len(per_model[model]):4d} faltando")
    for model in sorted(empty_models):
        if model not in per_model:
            print(f"  {model:9s}    0 faltando (GLE vazio)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
