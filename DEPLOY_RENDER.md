## Render deployment notes (SQLite)

This app uses SQLite by default:

- `APP_DB_PATH` (required on Render): set this to a **persistent disk path** (example: `/var/data/ahmed_cement.db`).
- `SECRET_KEY` (required): set a fixed value so sessions/remember cookies survive restarts.

If the app starts with the wrong/empty DB, logins may fail because the expected users are not in that database.

### Recommended Render env vars

- `APP_DB_PATH=/var/data/ahmed_cement.db`
- `SECRET_KEY=<long-random-string>`

### First boot only (creating a new DB)

If you are intentionally creating a new empty DB file, set:

- `ALLOW_EMPTY_DB=1`

After the DB exists and has real data, remove `ALLOW_EMPTY_DB` (or set it to `0`) to prevent accidental “empty DB” boots.

