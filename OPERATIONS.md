# Operations

## Super admin

- Single user. Bootstrapped via `flask seed-super-admin`. Never exposed in the
  UI — there is no "Add super admin" screen.
- All app configuration flows through this user: users, fleets, roles,
  vehicle categories, settings.
- `User.is_super_admin = True` on exactly one row, immutable after creation
  (only changeable via SQL or the CLI).

## Bootstrap a fresh install

```bash
docker compose up -d --build
docker compose exec web flask db upgrade
docker compose exec web flask seed-permissions
docker compose exec web flask seed-system-roles
docker compose exec web flask seed-default-categories
docker compose exec web flask seed-super-admin \
  --username oumar \
  --password 'change-me-on-first-login' \
  --email you@example.com   # optional
```

The `entrypoint.sh` runs `flask db upgrade` automatically — the other seed
commands are idempotent so you can re-run them safely on later deploys.

Usernames are lowercase, no spaces, the only thing a user types at login.
Email is optional and used only for password reset / notifications.

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
