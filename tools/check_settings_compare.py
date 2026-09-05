"""
Smoke script for the settings parser+comparator.

Not a test framework -- just a harness to run by hand:

    python3 tools/check_settings_compare.py

It covers 3 groups of scenarios:

  1. Synthetic cases with an expected verdict (pass/fail by equality).
  2. Compares a real SET_L1.TXT against itself (they must all be EQUAL).
  3. Compares the same SET_L1 between two distinct relays of the same family
     (shows the verdict distribution -- a human eye judges if it makes sense).

Output: a text table + an aggregate counter. Exit code 0 if no synthetic case
fails; it does not try to validate the production code beyond that.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Allows running as a script without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sellib.selogic.compare import compare
from sellib.settings import parse_settings_file

from pacct.paths import RDB_CACHE_DIR, RDBS_DIR

# Put each case as (a, b, kind, dialect, expected_verdict, label).
SYNTHETIC_CASES = [
    # ----------------------------------------------------------------- EQUAL
    ("A AND B", "A AND B", "logic", "keyword", "EQUAL",
     "identico"),
    ("A AND B # x", "A AND B # x", "logic", "keyword", "EQUAL",
     "identico com comentario"),
    ("A AND B", "A   AND   B", "logic", "keyword", "EQUAL",
     "whitespace nao importa"),
    ("Z1T+Z2T", "Z1T+Z2T", "logic", "symbolic", "EQUAL",
     "symbolic identico"),
    # ----------------------------------- EQUAL_LOGIC_DIFF_COMMENT
    ("A AND B # alfa", "A AND B # beta", "logic", "keyword",
     "EQUAL_LOGIC_DIFF_COMMENT", "mesmo corpo, comentarios diferentes"),
    ("A AND B", "A AND B # novo comentario", "logic", "keyword",
     "EQUAL_LOGIC_DIFF_COMMENT", "comentario adicionado"),
    # ------------------------------------------------------------- EQUIVALENT
    ("A AND B", "B AND A", "logic", "keyword", "EQUIVALENT",
     "AND comutativo"),
    ("A OR B OR C", "C OR A OR B", "logic", "keyword", "EQUIVALENT",
     "OR n-ario reorder"),
    ("A AND B AND C", "(A AND B) AND C", "logic", "keyword", "EQUIVALENT",
     "associativo AND com paren"),
    ("NOT NOT A", "A", "logic", "keyword", "EQUIVALENT",
     "double NOT"),
    ("NOT (A AND B)", "NOT A OR NOT B", "logic", "keyword", "EQUIVALENT",
     "De Morgan"),
    ("(A OR B) AND C", "A AND C OR B AND C", "logic", "keyword", "EQUIVALENT",
     "distributiva"),
    ("Z1T+Z2T", "Z2T+Z1T", "logic", "symbolic", "EQUIVALENT",
     "3xx commutativo"),
    ("!A*B", "B*!A", "logic", "symbolic", "EQUIVALENT",
     "3xx mix NOT e AND comutativo"),
    ("R_TRIG VB002 OR R_TRIG VB003", "R_TRIG VB003 OR R_TRIG VB002",
     "logic", "keyword", "EQUIVALENT",
     "R_TRIG comutativo (atomos distintos)"),
    # --------------------------------------------------------------- DIFFERENT
    ("A AND B", "A OR B", "logic", "keyword", "DIFFERENT",
     "AND vs OR"),
    ("A AND B", "A AND NOT B", "logic", "keyword", "DIFFERENT",
     "NOT em um operando"),
    ("Z1T+Z2T", "Z1T*Z2T", "logic", "symbolic", "DIFFERENT",
     "3xx OR vs AND"),
    ("R_TRIG VB002", "R_TRIG VB003", "logic", "keyword", "DIFFERENT",
     "edge em bits diferentes"),
    ("R_TRIG VB002", "VB002", "logic", "keyword", "DIFFERENT",
     "edge vs nivel"),
    # --------------------------------------------------------- numbers / enums
    ("0.000000", "0", "number", "keyword", "EQUIVALENT",
     "zero com tail de zeros"),
    ("1.000000", "1.0", "number", "keyword", "EQUIVALENT",
     "um com formatacao diferente"),
    ("0.5", "0.500000001", "number", "keyword", "EQUIVALENT",
     "diff < tolerancia rel"),
    ("0.5", "0.50001", "number", "keyword", "DIFFERENT",
     "diff > tolerancia rel"),
    ("Y", "Y", "enum", "keyword", "EQUAL", "enum identico"),
    ("Y", "N", "enum", "keyword", "DIFFERENT", "enum oposto"),
    ("S,T", "S,T", "enum", "keyword", "EQUAL",
     "enum multi-valor identico"),
    # ----------------------------------- non-boolean (math) with identical text
    ("(IA + IB + IC) / 3", "(IA + IB + IC) / 3", "logic", "keyword", "EQUAL",
     "math expr identica vira EQUAL via texto"),
    ("(IA + IB + IC) / 3", "(IB + IA + IC) / 3", "logic", "keyword", "DIFFERENT",
     "math nao tem equivalencia algebrica"),
    # ----------------------------------------------------- > 16 atoms (warning)
    (" OR ".join(f"A{i}" for i in range(20)),
     " OR ".join(f"A{i}" for i in range(19, -1, -1)),
     "logic", "keyword", "EQUIVALENT",
     "20 atomos: canonical match pega"),
    # ----------------------------------------------------- SER / set_list
    ("IN101,IN102,SV40", "IN101,IN102,SV40", "set_list", "keyword", "EQUAL",
     "SER identica"),
    ("IN101,IN102,SV40", "SV40,IN101,IN102", "set_list", "keyword", "EQUIVALENT",
     "SER reordenada"),
    ("IN101,IN102", "IN101,IN103", "set_list", "keyword", "DIFFERENT",
     "SER com wordbit trocado"),
    ("IN101,IN102,IN103", "IN101,IN102", "set_list", "keyword", "DIFFERENT",
     "SER com wordbit extra em A"),
]


def run_synthetic() -> int:
    """Runs the synthetic cases. Returns the number of failures."""
    print("=" * 80)
    print("CASOS SINTETICOS")
    print("=" * 80)
    fail = 0
    for a, b, kind, dialect, expected, label in SYNTHETIC_CASES:
        r = compare(a, b, kind=kind, dialect=dialect)
        ok = r.verdict == expected
        flag = "OK  " if ok else "FAIL"
        if not ok:
            fail += 1
        note = f" ({r.note})" if r.note else ""
        a_d = a if len(a) < 38 else a[:35] + "..."
        b_d = b if len(b) < 38 else b[:35] + "..."
        print(f"  [{flag}] {label}")
        print(f"         a={a_d!r:42}")
        print(f"         b={b_d!r:42}")
        print(f"         -> {r.verdict}{note}  (esperado {expected})")
    print()
    print(f"  Total: {len(SYNTHETIC_CASES)}  Falhas: {fail}")
    return fail


def run_self_compare() -> None:
    """Compares a real SET_L1 against itself -- everything must be EQUAL."""
    print()
    print("=" * 80)
    print("SELF-COMPARE (um SET_L1 real contra ele mesmo)")
    print("=" * 80)

    # The extractions live in the content cache (`cache/rdb/<sha256>/`);
    # `rdbs/` stays as a fallback for the old layout's extractions.
    candidates = []
    for root, pat in ((RDB_CACHE_DIR, "*/extracted/Relays/*"),
                      (RDBS_DIR, "extracted/*/Relays/*")):
        candidates += list(root.glob(f"{pat}/SET_L1.TXT"))
        candidates += list(root.glob(f"{pat}/set_L1.txt"))
    if not candidates:
        print("  (sem RDBs extraidos -- pulando)")
        return

    target = candidates[0]
    root = RDB_CACHE_DIR if RDB_CACHE_DIR in target.parents else RDBS_DIR
    relaytype_hint = ""
    ps = parse_settings_file(target)
    relaytype_hint = ps.relaytype or "?"
    print(f"  arquivo: {target.relative_to(root)}")
    print(f"  RELAYTYPE: {relaytype_hint}, secao={ps.section}, linhas={len(ps.lines)}")

    dialect = "symbolic" if (relaytype_hint or "").startswith(("SEL-3", "3", "0311")) else "keyword"
    print(f"  dialeto inferido: {dialect}")

    counter: Counter[str] = Counter()
    for ln in ps.lines:
        # Everything is "logic" to keep the self-compare simple: the verdict
        # for numbers will also be EQUAL if the strings are identical.
        r = compare(ln.value, ln.value, kind="logic", dialect=dialect)
        counter[r.verdict] += 1
    print(f"  veredictos: {dict(counter)}")
    if list(counter.keys()) != ["EQUAL"]:
        print("  AVISO: nem tudo deu EQUAL -- isso provavelmente eh um bug.")


def run_cross_compare() -> None:
    """Compares the same SET_L1 between 2 relays of the same model."""
    print()
    print("=" * 80)
    print("CROSS-COMPARE (2 reles 7xx mesmo modelo)")
    print("=" * 80)

    # Looks for two SEL-751 in the same extraction
    targets: list[Path] = []
    for cand in sorted(list(RDB_CACHE_DIR.glob("*/extracted/Relays/*/set_L1.txt"))
                       + list(RDBS_DIR.glob("extracted/*/Relays/*/set_L1.txt"))):
        try:
            ps = parse_settings_file(cand)
        except Exception:
            continue
        rt = ps.relaytype or ""
        if "751" in rt or "787" in rt:
            targets.append(cand)
        if len(targets) >= 2:
            break

    if len(targets) < 2:
        print("  (menos de 2 SEL-751 disponiveis -- pulando)")
        return

    a_path, b_path = targets[0], targets[1]
    a = parse_settings_file(a_path)
    b = parse_settings_file(b_path)
    print(f"  A: {a_path.parent.name}  ({a.relaytype})")
    print(f"  B: {b_path.parent.name}  ({b.relaytype})")

    by_key_a = {ln.key: ln.value for ln in a.lines}
    by_key_b = {ln.key: ln.value for ln in b.lines}
    common = sorted(set(by_key_a) & set(by_key_b))
    print(f"  chaves comuns: {len(common)}  somente em A: {len(set(by_key_a) - set(by_key_b))}"
          f"  somente em B: {len(set(by_key_b) - set(by_key_a))}")

    counter: Counter[str] = Counter()
    differing: list[tuple[str, str, str, str]] = []
    for k in common:
        # Boolean attempt first; if it does not parse, it falls back to
        # number/string via text. We simplify here: SET_L1/set_L1 are
        # predominantly logic expressions.
        r = compare(by_key_a[k], by_key_b[k], kind="logic", dialect="keyword")
        counter[r.verdict] += 1
        if r.verdict == "DIFFERENT":
            differing.append((k, by_key_a[k], by_key_b[k], r.note or ""))

    print(f"  veredictos: {dict(counter)}")
    if differing:
        print("  amostra de DIFFERENT (ate 5):")
        for k, va, vb, note in differing[:5]:
            note_suffix = f"  [{note}]" if note else ""
            print(f"    {k:10}  A={va[:60]!r}")
            print(f"    {' '*10}  B={vb[:60]!r}{note_suffix}")


def main() -> int:
    failures = run_synthetic()
    run_self_compare()
    run_cross_compare()
    print()
    print("=" * 80)
    if failures == 0:
        print("RESULTADO: OK (casos sinteticos passaram)")
        return 0
    print(f"RESULTADO: {failures} falhas nos casos sinteticos")
    return 1


if __name__ == "__main__":
    sys.exit(main())
