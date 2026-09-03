# NOTICE

PAC CT — Protection, Automation & Control Commissioning Toolkit
Copyright (C) 2026 Guilherme Marini

Licensed under the **GNU Affero General Public License, version 3 or later**
(AGPL-3.0-or-later). The full text is in [LICENSE](LICENSE).

## Why AGPL

Not a preference — an obligation already in the tree. The GLV's MMS transport
(`pacct/web/glv/transport/mms.py`) imports **py61850**, which is
AGPL-3.0-or-later. PAC CT is served over a network, so §13 is the operative
clause, and a public repository satisfies it by construction.

## Vendored code

**selprotopy** — MIT, Copyright (c) 2020 Joe Stanley
<https://github.com/engineerjoe440/sel-proto-py>

A copy lives in `selprotopy/` at the project root and is **patched**: it is not
the upstream release. The changes are what make SEL Fast Message work against
the relays this toolkit talks to. Every private of it that PAC CT touches is
inventoried in `pacct/core/relay_conn.py`'s module docstring, so the coupling
is written down in one place rather than spread through the tree.

Its licence is kept verbatim at [`docs/LICENSE-selprotopy`](docs/LICENSE-selprotopy).
MIT permits this redistribution; the attribution above and that file are what
it asks for in return.

## Fonts

Nine `.woff2` faces ship in `src/pacct/web/static/fonts/`, deliberately: a
substation may have no internet, so no page may ask a CDN for a font. All are
under the **SIL Open Font License 1.1**:

- Courier Prime, IBM Plex Mono, IBM Plex Sans, Public Sans, Roboto,
  Roboto Condensed

Their licences are in `src/pacct/web/static/fonts/licencas/` and must keep
travelling with the files. `src/pacct/web/static/fonts/NOTICE.md` carries the
per-family detail.

## Libraries extracted from this project

Both are AGPL-3.0-or-later, same copyright holder, and were part of this
repository before they were split out:

- **cfbwrite** — <https://github.com/GuilhermeMarini/cfbwrite>
- **selfiles** — <https://github.com/GuilhermeMarini/selfiles>

## Dependencies

| package | version pinned | licence |
|---|---|---|
| py61850 | >= 0.2.0.dev1 | **AGPL-3.0-or-later** |
| olefile | >= 0.47 | BSD |
| openpyxl | >= 3.1 | MIT |
| pyserial | >= 3.5 | BSD |
| telnetlib3 | >= 4.0.2 | ISC |

py61850 was MIT until commit `e515aca` and is AGPL from there on, with a
commercial option available from its copyright holder. That change is the
reason for the licence on this project.

## Not distributed here

SEL instruction manuals and DNP3 device profile bundles are the
manufacturer's copyrighted material and are **not** in this repository. The
per-model data derived from them (`selfiles`'s `wordbits/`) is ours; the source
documents are not redistributed.
