#!/usr/bin/env bash
set -euo pipefail

CANONICAL="$(cd "$(dirname "$0")/.." && pwd)"
ADAPTERS="${CANONICAL}/adapters"
BACKUP_ROOT="${CANONICAL}/.install-backup-$(date +%Y%m%d-%H%M%S)"
installed=0
skipped=0

say() { printf '==> %s\n' "$*"; }

link_or_copy() {
  local target="$1" dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    if [ -L "$dest" ] && [ "$(readlink "$dest")" = "$target" ]; then
      say "already installed: $dest"
      skipped=$((skipped+1))
      return 0
    fi
    local backup_dir="${BACKUP_ROOT}$(dirname "$dest")"
    mkdir -p "$backup_dir"
    cp -Rp "$dest" "${backup_dir}/$(basename "$dest")" || {
      say "ERROR: could not back up existing $dest; aborting without changes"
      exit 1
    }
    say "backed up existing $dest -> ${backup_dir}/$(basename "$dest")"
    rm -rf "$dest"
  fi
  ln -s "$target" "$dest"
  say "linked $dest -> $target"
  installed=$((installed+1))
}

command -v claude >/dev/null 2>&1 && HAVE_CLAUDE=1 || HAVE_CLAUDE=0
command -v opencode >/dev/null 2>&1 && HAVE_OC=1 || HAVE_OC=0
command -v codex >/dev/null 2>&1 && HAVE_CODEX=1 || HAVE_CODEX=0
command -v pi >/dev/null 2>&1 && HAVE_PI=1 || HAVE_PI=0

if [ "$HAVE_CLAUDE" = 1 ]; then
  link_or_copy "$CANONICAL" "${HOME}/.claude/skills/requirements-miner"
fi

if [ "$HAVE_CLAUDE" = 1 ]; then
  link_or_copy "${ADAPTERS}/claude/rm-expert.md" "${HOME}/.claude/agents/rm-expert.md"
fi
if [ "$HAVE_OC" = 1 ]; then
  link_or_copy "${ADAPTERS}/opencode/rm-expert.md" "${HOME}/.config/opencode/agents/rm-expert.md"
fi
if [ "$HAVE_CODEX" = 1 ]; then
  link_or_copy "${ADAPTERS}/codex/rm_expert.toml" "${CODEX_HOME:-${HOME}/.codex}/agents/rm_expert.toml"
fi
if [ "$HAVE_PI" = 1 ]; then
  link_or_copy "${ADAPTERS}/pi/rm-expert.md" "${HOME}/.pi/agent/agents/rm-expert.md"
fi

say "done: ${installed} installed, ${skipped} already present"
