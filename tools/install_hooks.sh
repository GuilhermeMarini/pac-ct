#!/usr/bin/env bash
# Installs this project's git hooks into `.git/hooks/`.
#
# Hooks are not versioned by git, so the tree carries them in `tools/hooks/`
# and this copies them across. Run it once per clone:
#
#     tools/install_hooks.sh
#
# It copies rather than symlinks: a symlink into the work tree breaks the
# moment somebody checks out a branch without the file, and a hook that
# silently stops running is worse than one that is out of date.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
SRC="$ROOT/tools/hooks"
DEST="$(git rev-parse --git-path hooks)"

mkdir -p "$DEST"
for hook in "$SRC"/*; do
  name="$(basename "$hook")"
  cp "$hook" "$DEST/$name"
  chmod +x "$DEST/$name"
  echo "[OK] $name -> $DEST/$name"
done
