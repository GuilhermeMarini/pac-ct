#!/usr/bin/env python3
"""Which Relay Word bits of each relay model have no IEC 61850 address?

Writes `fixtures/model_missing.txt`.

    python3 tools/relay_word_vs_61850.py
    python3 tools/relay_word_vs_61850.py --scd "rdbs/SE X.scd" -o /tmp/out.txt

Baseline (the Relay Word), per model, in priority order:

  1. a GLV FID cache `cache/<FID>.json` -- what `AsciiTargetReader` mapped
  2. the `bits` of `data/wordbits/<MODEL>.json` -- same origin, already merged

Both come off a real relay, never off a manual. A model with neither is listed
as uncomputable rather than guessed at.

The 61850 side is the union of the project SCD's `sAddr` for IEDs of that model
and the factory ICD map for the same part.

**The baseline is not the same size for every family, and that is not a defect.**
`fast_read = "fast_meter_digitals"` (the 7xx) has no TARGET region: its Relay
Word arrives inside the A5D1 Fast Meter DNA block, which carries far fewer names
than the SCL `sAddr` map does. Measured on a live SEL-751-R402-V2: 1116 digitals
over telnet against 1903 names in the ICD map, and the 1200 the DNA block omits
include all 256 `VB` virtual bits -- the GOOSE pages the GLV cannot show today.
So for a 7xx a baseline smaller than the map is STRUCTURAL. Only for the
`target_region` / `tar_digitals` families does that gap mean a truncated capture.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _sel61850 as maps  # noqa: E402
from pacct.core import relay_models  # noqa: E402
from pacct.paths import (  # noqa: E402
    CACHE_DIR,
    FIXTURES_DIR,
    RDBS_DIR,
    WORDBITS_DIR,
)

ICD_MAP = FIXTURES_DIR / "ICD files" / "SEL" / "wordbits.json"
OUT = FIXTURES_DIR / "model_missing.txt"

# A 7xx reads its digitals out of the Fast Meter DNA block; the others walk a
# TARGET region. Only the latter can be "partially" captured.
FAST_METER = "fast_meter_digitals"


def fid_caches() -> dict:
    out = {}
    for f in sorted(glob.glob(str(CACHE_DIR / "SEL-*.json"))):
        try:
            raw = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        names = {n.upper() for ns in (raw.get("row_to_names") or {}).values()
                 for n in ns if n and n != "*"}
        if names:
            out[Path(f).stem] = names
    return out


def registry_bits() -> dict:
    out = {}
    for f in sorted(glob.glob(str(WORDBITS_DIR / "*.json"))):
        raw = json.loads(Path(f).read_text(encoding="utf-8"))
        bits = {x.upper() for x in (raw.get("bits") or [])}
        if bits:
            out[Path(f).stem] = bits
    return out


def collect_models() -> tuple[dict, dict]:
    """model -> (relay word bits, source label, icd part); plus its other FIDs."""
    models, alternates = {}, collections.defaultdict(list)
    for fid, bits in sorted(fid_caches().items()):
        m = re.match(r"SEL-([0-9]+[A-Z]*)(?:-([0-9A-Z]+))?-R\d+", fid)
        if not m:
            continue
        base, variant = m.group(1), m.group(2)
        part = f"311C{variant}" if base.startswith("311C") and variant else base
        key = f"SEL-{base}"
        alternates[key].append(fid)
        # Several captures of one model: keep the LARGEST, which is the most
        # complete. Taking whichever the glob yielded first made the numbers
        # depend on directory order.
        if key not in models or len(bits) > len(models[key][0]):
            models[key] = (bits, f"cache GLV {fid}", maps.norm_part(part))
    for name, bits in registry_bits().items():
        if name not in models:
            models[name] = (bits, f"data/wordbits/{name}.json",
                            maps.norm_part(name.replace("SEL-", "")))
    return models, alternates


def fast_read_of(model: str) -> str:
    rm = relay_models.lookup(model)
    return getattr(rm, "fast_read", "target_region") if rm else "target_region"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scd", type=Path, default=None,
                    help="SCD do projeto (padrao: o .scd mais recente em rdbs/)")
    ap.add_argument("--icd", type=Path, default=ICD_MAP)
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    args = ap.parse_args()

    scd_path = args.scd
    if scd_path is None:
        found = sorted(RDBS_DIR.glob("*.scd"), key=lambda p: p.stat().st_mtime)
        scd_path = found[-1] if found else None
    scd = maps.load_scd(scd_path) if scd_path else {"bits": {}, "part": {},
                                                    "cfgver": {}, "name": {}}
    icd, icd_groups = maps.load_icd(args.icd)

    # An IED's sAddr set belongs to its PART, so every IED of one part folds
    # together -- three 451s in a substation describe the same relay model.
    scd_by_part: dict = collections.defaultdict(set)
    scd_ieds: dict = collections.defaultdict(list)
    scd_cv: dict = collections.defaultdict(set)
    for key, bits in scd["bits"].items():
        part = scd["part"].get(key)
        if not part or not bits:
            continue
        scd_by_part[part] |= bits
        scd_ieds[part].append(scd["name"][key])
        scd_cv[part].add(scd["cfgver"][key])

    models, alternates = collect_models()

    L: list = []
    A = L.append
    A("=" * 74)
    A("BITS DA RELAY WORD SEM ENDERECO IEC 61850")
    A("=" * 74)
    A("")
    A(f"Gerado em {datetime.date.today().isoformat()} por "
      f"tools/{Path(__file__).name}.")
    A(f"SCD: {scd_path.name if scd_path else '(nenhum)'}")
    A("")
    A("METODO")
    A("  Base (Relay Word)  cache GLV `cache/<FID>.json` quando existe; senao os")
    A("                     `bits` de `data/wordbits/<MODELO>.json`. Ambos vem de")
    A("                     um rele real -- nao de documentacao.")
    A("  Lado 61850         uniao do sAddr=\"db:NOME\" dos IEDs daquele modelo no")
    A("                     SCD do projeto com o mapa de fabrica do ICD.")
    A("  Faltando           bit que existe na Relay Word e nao tem sAddr em")
    A("                     nenhuma das duas: nao ha endereco MMS pra ele.")
    A("")
    A("  O sAddr e' atributo de SCL e o rele NAO o serve por MMS (verificado no")
    A("  SEL-451-5 R331: $DC$Ind01$d -> object-non-existent), entao o mapa so vem")
    A("  de arquivo -- nunca de descoberta no rele, como o TAR faz no telnet.")
    A("")
    A("  ATENCAO AO 7xx: um rele `fast_read=fast_meter_digitals` nao tem regiao")
    A("  TARGET; a Relay Word dele chega pelo bloco DNA do Fast Meter, que carrega")
    A("  MENOS nomes que o mapa SCL. Base menor que o mapa e' ESTRUTURAL nessa")
    A("  familia, nao captura parcial -- e o que o mapa tem a mais inclui os 256")
    A("  VB, que o telnet nao le de jeito nenhum.")
    A("")

    rows, blocks = [], []
    for model in sorted(models):
        word, src, part = models[model]
        s_bits = scd_by_part.get(part, set())
        i_bits = icd.get(part, set())
        mapped = s_bits | i_bits
        missing = sorted(b for b in word if b not in mapped)
        covered = len(word) - len(missing)
        cov = covered / len(word) if word else 0.0
        mode = fast_read_of(model)
        smaller = bool(mapped) and len(word) < len(mapped)
        # Only a TARGET/TAR family can be truncated; the 7xx is smaller by design.
        partial = smaller and mode != FAST_METER
        rows.append((model, len(word), len(mapped), covered, len(missing),
                     cov, bool(s_bits), bool(i_bits), partial, mode))

        B = blocks.append
        B("=" * 74)
        B(f"{model}   --   {len(missing)} de {len(word)} bits sem endereco 61850 "
          f"({1 - cov:.1%})")
        B("=" * 74)
        B(f"  Relay Word : {len(word):5d} bits   fonte: {src}")
        B(f"               fast_read={mode}")
        others = [f for f in alternates.get(model, []) if f not in src]
        if others:
            B(f"               outras capturas: {', '.join(others)}")
        if s_bits:
            B(f"  SCD        : {len(s_bits):5d} bits   IEDs: "
              f"{', '.join(sorted(scd_ieds[part]))}")
            B(f"               configVersion: {', '.join(sorted(scd_cv[part]))}")
        else:
            B("  SCD        :     -          nenhum IED desse modelo no SCD")
        if i_bits:
            B(f"  ICD        : {len(i_bits):5d} bits   grupos: "
              f"{', '.join(icd_groups.get(part, []))}")
        else:
            B(f"  ICD        :     -          sem mapa pra peca '{part}'")
        if smaller and mode == FAST_METER:
            B("")
            B("  NOTA: a base tem MENOS bits que o mapa 61850, e nisso esta certa.")
            B("  Um 7xx le os digitais do bloco DNA do Fast Meter, que carrega")
            B("  menos nomes que o sAddr do SCL. Nao e' captura parcial: o que o")
            B("  mapa tem a mais o telnet nao alcanca (os 256 VB, entre outros),")
            B("  entao aqui o MMS ve MAIS que o telnet, nao menos.")
        elif partial:
            B("")
            B("  ATENCAO: a base tem MENOS bits que o mapa 61850 num modelo que le")
            B("  a regiao TARGET -- sinal de captura parcial. O numero de FALTANDO")
            B("  esta subestimado; refaca a captura abrindo o rele no GLV.")
        B("")
        B(f"  COBERTO    : {covered:5d} ({cov:.1%})")
        B(f"  FALTANDO   : {len(missing):5d} ({1 - cov:.1%})")
        B("")
        if not mapped:
            B("  Sem mapa 61850 pra este modelo -- nada a comparar.")
            B("")
            continue

        fam: dict = collections.defaultdict(lambda: [0, 0])
        for b in word:
            f = maps.family_of(b)
            fam[f][0] += 1
            fam[f][1] += 1 if b in mapped else 0
        B("  COBERTURA POR FAMILIA")
        B(f"    {'familia':42s} {'RW':>5s} {'61850':>6s} {'%':>5s}")
        for f in sorted(fam, key=lambda x: (-(fam[x][0] - fam[x][1]), x)):
            tot, c = fam[f]
            B(f"    {f:42s} {tot:5d} {c:6d} {c / tot:5.0%}")
        B("")
        B("  BITS FALTANDO, POR FAMILIA")
        for f, names in maps.by_family(missing):
            B(f"    -- {f}  ({len(names)})")
            blocks.extend(maps.columns(names))
            B("")
        B("")

    A("RESUMO")
    A(f"  {'modelo':12s} {'Relay Word':>11s} {'mapa 61850':>11s} {'coberto':>8s} "
      f"{'faltando':>9s} {'cobertura':>10s}  fontes")
    for (model, nw, nm, nc, nmiss, cov, has_s, has_i, partial, _mode) in rows:
        src = ("SCD+ICD" if has_s and has_i else "SCD" if has_s
               else "ICD" if has_i else "nenhuma")
        A(f"  {model:12s} {nw:11d} {nm:11d} {nc:8d} {nmiss:9d} {cov:9.1%}  "
          f"{src}{'   (*)' if partial else ''}")
    if any(r[8] for r in rows):
        A("")
        A("  (*) base menor que o mapa num modelo de regiao TARGET -- captura")
        A("      parcial, FALTANDO subestimado. Veja o bloco do modelo.")
    A("")
    A("MODELOS SEM BASE (nao da pra calcular)")
    A("  Um modelo so entra na tabela acima quando existe uma captura REAL da")
    A("  Relay Word dele. Sem isso nao ha o que subtrair, e inventar a partir do")
    A("  manual seria pior que a ausencia.")
    have = {models[m][2] for m in models}
    parts = sorted(set(icd) - have)
    A(f"  {len(parts)} pecas tem mapa 61850 mas nenhuma captura de Relay Word:")
    L.extend(maps.columns(parts, per_line=8, width=12, indent="    "))
    A("")
    A("  Pra incluir uma: abra o rele uma vez no GLV por telnet -- o")
    A("  AsciiTargetReader grava `cache/<FID>.json` -- e rode este gerador de novo.")
    A("")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(L) + "\n" + "\n".join(blocks) + "\n",
                        encoding="utf-8")
    print(f"{args.out}: {args.out.stat().st_size / 1024:.1f} KB")
    for (model, nw, _nm, _nc, nmiss, cov, *_rest) in rows:
        print(f"  {model:12s} faltando {nmiss:5d}/{nw:5d} ({1 - cov:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
