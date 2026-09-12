# Operations

## Super admin

- Single user. Bootstrapped via `flask seed-super-admin`. Never exposed in the
  UI — there is no "Add super admin" screen.
- All app configuration flows through this user: users, fleets, roles,
  vehicle categories, settings.
- `User.is_super_admin = True` on exactly one row, immutable after creation
  (only changeable via SQL or the CLI).

## Bootstrap a fresh install

1. Copy `.env.example` to `.env` and fill in at minimum:
   - `DB_PASSWORD` (matches the value in `DATABASE_URL`)
   - `SECRET_KEY` (long random string)
   - `ADMIN_USERNAME` (e.g. `admin`)
   - `ADMIN_PASSWORD` (used once to create the super admin — change it after first login)
   - `ADMIN_EMAIL` and `ADMIN_FULL_NAME` (optional)

2. Build and start:
   ```bash
   docker compose up -d --build
   ```

`entrypoint.sh` then does this on container start, every time:

```
flask db upgrade   # apply any new migrations
flask seed         # idempotent: permissions, roles, categories, settings, super admin
```

The `seed` command refuses to overwrite an existing super admin, so it's
safe to leave `ADMIN_PASSWORD` set in `.env` across redeploys.

Usernames are lowercase, no spaces, the only thing a user types at login.
Email is optional and used only for password reset / notifications.

### Manual super admin bootstrap (alternative)

If you don't want credentials in `.env`, leave `ADMIN_PASSWORD` empty and
bootstrap from the CLI:

```bash
docker compose exec web flask seed-super-admin \
  --username admin \
  --password 'one-time-temporary-password'
```

## Onboard a new staff user

1. Log in as super admin → `/admin/users` → **New user**
2. Choose a username + temporary password (email optional)
3. Assign one or more fleets, picking a role per fleet
4. Send the credentials privately to the user

## Break-glass

### Reset super admin password

```bash
docker compose exec web flask reset-super-admin-password --password 'new-pw'
```

### Grant emergency Fleet Manager access via CLI

```bash
docker compose exec web flask grant-fleet --user mamadou \
  --fleet transport --role 'Fleet Manager'
```

Useful if the super admin is unavailable and a fleet needs an urgent role
change.

### Restore a deleted system role

System roles (Fleet Manager, Supervisor, Inspector, External) can be deleted
from /admin/roles. Because `entrypoint.sh` runs `flask seed` on every boot,
the deletion is remembered in the `deleted_system_roles` app setting so the
seeder does not bring the role back. To undo that:

```bash
docker compose exec web flask restore-system-roles
```

This clears the record and re-seeds every deleted system role with its
original permissions. `flask seed-system-roles` prints which roles are
currently held back.

### Transfer super admin to another user

Run SQL on the production DB:

```sql
UPDATE users SET is_super_admin = FALSE WHERE username = 'old_admin';
UPDATE users SET is_super_admin = TRUE  WHERE username = 'new_admin';
```

Then have the new super admin reset their password via the CLI.

## Logs

```bash
docker compose logs -f web        # follow Flask logs
docker compose logs --tail=200 web | grep -E "S3|upload|ERROR"
```

## Database backups

Everything the company has recorded lives in one Postgres volume on one server.
The Docker volume survives a redeploy; it does not survive a failed disk, a
`docker compose down -v`, or the server being lost. These backups are the copy
that lives somewhere else.

### What happens

A dump is taken, gzipped and sent to object storage, into its own `backups/`
folder foldered by month — nowhere near the photos. The dump never touches the
server's disk: it is piped from the database container straight to the upload.

Kept: **every dump of the last 30 days**, then **the first dump of each month
for a year**. Older ones are deleted. A file whose name the rotation cannot
read is never deleted, so nothing else in the bucket is ever at risk.

### Set it up

Object storage must be configured (`S3_*` in `.env`) — the same bucket and keys
the photos use. Then, once, on the server:

```bash
crontab -e
```

and add, adjusting the path:

```cron
# Backup zone_web every night at 03:00
0 3 * * * cd /srv/zone_web && ./scripts/backup-db.sh >> /var/log/zone-backup.log 2>&1
```

### Check it

```bash
./scripts/backup-db.sh                                    # run one now
docker compose exec -T web python scripts/backup_db.py --list
tail -20 /var/log/zone-backup.log
```

`--list` prints every stored dump with its size and date. A dump of a few
kilobytes means the database is nearly empty; a dump of a few bytes never gets
uploaded at all — the script refuses anything too small to be real, because a
failed `pg_dump` still produces a valid, tiny, useless gzip, and replacing a
good backup with one of those is the only way this can quietly fail.

### Restore

**This replaces everything in the database.** It asks for the database name
before doing anything, and it keeps the current database aside first, in
`backup-before-restore-<date>.sql.gz`, in case the wrong backup was chosen.

```bash
./scripts/restore-db.sh                                   # list what is there
./scripts/restore-db.sh backups/2026-09/zone-2026-09-12-0300.sql.gz
```

Afterwards, check the app opens and the figures look right, then delete the
safety file.

### Thin the folder without taking a backup

```bash
docker compose exec -T web python scripts/backup_db.py --rotate-only --dry-run
docker compose exec -T web python scripts/backup_db.py --rotate-only
```
