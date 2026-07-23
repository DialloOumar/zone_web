# Zone Web

Fleet tracking app for a single mining-site client (Guinea). Built on the same
Flask + Postgres + Docker stack as `batmex_web`, with a domain model focused on
day-by-day per-vehicle fuel / trips / hours / km logging, configurable
maintenance rules, and a unified expense view.

## What it does

- Tracks buses, minibuses, navette, citerne, service vehicles, TSF trucks and
  TSF machines under a single project. Vehicle behaviour differs by
  `Vehicle.category`.
- Groups categories into admin-defined **Fleets** for staff access control
  (`Transport personnel`, `Engins TSF`, …).
- Per-fleet **Roles** with on-the-fly creation and a tri-state permission grid
  (`Cannot` / `Direct` / `Approval`) per (resource × action).
- **Approval queue** for sensitive changes, with a 15-minute grace window for
  self-correction by the original creator.
- **Maintenance rules** (km / hours / time / anomaly) that surface real-time
  alerts to fleet managers.
- **Expenses** unified across fuel / maintenance / other, with cost-per-km KPI
  and back-chargeable flag for client billing.

## Roles at a glance

| Concept | What it can do |
|---|---|
| **Super Admin** (you) | Only role that configures users, fleets, roles, categories, settings. CLI-bootstrapped, never exposed in the UI. |
| **Fleet Manager** (`can_approve=True`) | Approves pending changes for assigned fleet(s). Operational, not configurational. |
| **Custom roles** | Any combination of permissions an admin builds for staff. |

## Stack

- **Backend**: Flask 3 + SQLAlchemy 2 + Flask-Migrate (Alembic)
- **Database**: Postgres 16
- **Auth**: Flask-Login + bcrypt
- **i18n**: Server-side French / English (`languages.py`)
- **Storage**: Linode Object Storage via `boto3`, slip / receipt photos compressed client-side
- **Printing**: browser print view (`/entries/export.print`); "Save as PDF" in the print dialog when a file is needed
- **Frontend**: server-rendered Jinja, vanilla JS for interactions
- **Container**: Docker Compose for local dev + prod parity

## Getting started

```bash
cp .env.example .env       # fill in DB_PASSWORD, SECRET_KEY, S3 keys
docker compose up --build
docker compose exec web flask seed-super-admin --email you@... --password ...
```

Then visit http://localhost:8000.

## Project layout

```
zone_web/
├── app.py              # Flask app, routes, CLI commands
├── models.py           # SQLAlchemy models — 17 tables
├── languages.py        # FR/EN translation dictionaries
├── s3_storage.py       # Photo upload/sign helpers (Linode S3)
├── migrations/         # Alembic
├── templates/          # Jinja templates
├── static/             # CSS, JS, images
├── scripts/            # one-off helpers (e.g. data import)
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
└── requirements.txt
```

## Operations

See `OPERATIONS.md` for super-admin bootstrap, role reset, and break-glass
procedures.

## License

Private / client-licensed.
