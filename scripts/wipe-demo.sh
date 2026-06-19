#!/usr/bin/env bash
# Permanently remove the demo data created by seed-demo.sh — the Zone Nord /
# Zone Sud fleets and everything under them, plus the demo 'manager' user.
# Only the demo fleets are touched; real fleets and their data are left intact.
#
# Usage:  ./scripts/wipe-demo.sh        # asks for confirmation
#         ./scripts/wipe-demo.sh --yes  # no prompt (for CI / scripts)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--yes" ]; then
    docker compose exec web flask wipe-demo --yes
else
    # -T not used so the confirmation prompt is interactive.
    docker compose exec web flask wipe-demo
fi
