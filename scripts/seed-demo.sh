#!/usr/bin/env bash
# Seed demo data into the running stack (Zone Nord / Zone Sud fleets, vehicles,
# operators, ~80 days of activity, expenses, maintenance, alerts, and a demo
# 'manager' user). Safe to re-run on a fresh DB; pass --force to add anyway.
#
# Usage:  ./scripts/seed-demo.sh            # default 80 days
#         ./scripts/seed-demo.sh --days 120
#         ./scripts/seed-demo.sh --force
#
# Remove it again with ./scripts/wipe-demo.sh
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec web flask seed-demo "$@"
