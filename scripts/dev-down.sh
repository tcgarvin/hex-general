#!/usr/bin/env bash
#
# Stop the local Supabase stack and restore the frontend env that dev-up.sh
# redirected at the local stack. Counterpart to scripts/dev-up.sh (Phase 7,
# Slice 3). See docs/LOCAL_DEV.md.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FE_ENV="$REPO/frontend/.env.local"

if command -v supabase >/dev/null 2>&1; then
  SUPABASE="supabase"
elif [ -x "$HOME/.local/bin/supabase" ]; then
  SUPABASE="$HOME/.local/bin/supabase"
else
  echo "error: supabase CLI not found." >&2
  exit 1
fi

echo "Stopping local Supabase stack..."
"$SUPABASE" stop --workdir "$REPO"

# Restore the original frontend env (the one pointing at the deployed project),
# if dev-up.sh backed it up.
if [ -f "$FE_ENV.bak" ]; then
  mv "$FE_ENV.bak" "$FE_ENV"
  echo "Restored frontend/.env.local from .env.local.bak"
fi

echo "Local stack stopped. (api/.env is left in place; it is gitignored.)"
