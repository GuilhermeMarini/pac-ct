#!/usr/bin/env bash
# PAC CT launcher.
#
# Lives at the root of the install, BESIDE `current`, `versions/` and
# `userdata/` -- `app.py --instalar` puts it there. It never names a version:
# `current` is the pointer, and an update is a repointing, which is also what
# makes a rollback one command.
#
#   PAC-CT/
#   |-- pac-ct.sh          <-- you are here
#   |-- versions/1.4.0/
#   |-- current -> versions/1.4.0
#   `-- userdata/          config.ini, cache/, rdbs/ -- never touched by an update
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -d "$HERE/current" ]; then
  VERSION_DIR="$HERE/current"
  DATA_DIR="$HERE/userdata"
elif [ -f "$HERE/app.py" ]; then
  # Straight out of the zip, before `--instalar` ran. Useful for a one-off
  # look at a version without installing it.
  VERSION_DIR="$HERE"
  DATA_DIR="$HERE"
else
  echo "[ERRO] Nao encontrei 'current' nem 'app.py' em $HERE." >&2
  echo "       Descompacte o pacote em PAC-CT/versions/<versao>/ e rode:" >&2
  echo "       python3 PAC-CT/versions/<versao>/app.py --instalar" >&2
  exit 1
fi

# PACCT_ROOT is the version being run; PACCT_DATA_DIR is the engineer's data,
# which is outside it on purpose (see src/pacct/paths.py).
export PACCT_ROOT="$VERSION_DIR"
export PACCT_DATA_DIR="$DATA_DIR"

PY="$VERSION_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

exec "$PY" "$VERSION_DIR/app.py" --web "$@"
