# AGENTS.md

## Cursor Cloud specific instructions

TalkCash is a single product: a FastAPI backend (`backend/`) plus a React Native / Expo mobile client (`mobile/`). Standard dev/test/run commands live in `README.md`; this section only covers non-obvious cloud-VM caveats.

### Services run natively (not via Docker)

The cloud VM has **PostgreSQL 16, Redis 7, and Tesseract (incl. Turkish lang pack)** installed natively in the snapshot, instead of using `docker-compose.yml`. Python deps live in the repo-root virtualenv `/workspace/.venv` (refreshed by the startup/update script).

There is **no systemd** in the VM, so services must be started manually (they are NOT auto-started and NOT part of the update script):

```bash
sudo pg_ctlcluster 16 main start     # PostgreSQL on :5432
redis-server --daemonize yes         # Redis on :6379
```

The `talkcash` Postgres role (password `talkcash`) and the `talkcash` + `talkcash_test` databases already exist in the snapshot. If they are ever missing, recreate with:
`sudo -u postgres psql -c "CREATE ROLE talkcash LOGIN PASSWORD 'talkcash' CREATEDB;"` then `sudo -u postgres createdb -O talkcash talkcash` and `... talkcash_test`.

### Env files (gitignored)

`backend/.env` and `mobile/.env` are gitignored and must point at **localhost**, not the docker-compose hostnames `db`/`redis`. They are created during setup; if missing, recreate:
- `backend/.env`: `DATABASE_URL=postgresql+asyncpg://talkcash:talkcash@localhost:5432/talkcash`, `REDIS_URL=redis://localhost:6379/0`, `SECRET_KEY=dev-secret-key-local`, `S3_ENABLED=false` (local filesystem `uploads/` instead of MinIO).
- `mobile/.env`: copy from `mobile/.env.example` (default `EXPO_PUBLIC_API_URL=http://localhost:8000/api/v1`).

### Running the backend (dev, hot-reload)

```bash
cd backend && alembic upgrade head        # use /workspace/.venv/bin/alembic
/workspace/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Health: `http://localhost:8000/health` (expect `status: ok`, `database`+`redis` true). Interactive docs: `http://localhost:8000/docs`. Use the venv's binaries (`/workspace/.venv/bin/...`) for `alembic`, `uvicorn`, `pytest`, `python`.

### Tests

- Backend tests must point at the **`talkcash_test`** DB and disable scheduler/rate-limit (see `README.md`):
  `cd backend && RATE_LIMIT_ENABLED=false SCHEDULER_ENABLED=false DATABASE_URL=postgresql+asyncpg://talkcash:talkcash@localhost:5432/talkcash_test /workspace/.venv/bin/python -m pytest tests/ -q`
  The e2e suite auto-skips if Postgres is unreachable, so a green run requires Postgres running.
- Mobile lint/test: `cd mobile && npx tsc --noEmit && npm test` (there is no separate `lint` script — `tsc --noEmit` is the typecheck/lint gate).

### Mobile (Expo) gotchas

- `npm install` nests `expo-asset` under `mobile/node_modules/expo/node_modules/` instead of hoisting it, which breaks `npx expo start` with "The required package `expo-asset` cannot be found". The startup/update script recreates a top-level symlink `mobile/node_modules/expo-asset -> expo/node_modules/expo-asset`. If `expo start` fails this way after a manual `npm install`, recreate that symlink.
- The VM is headless with no Android/iOS emulator or device, so the mobile UI cannot be rendered here. Validate the mobile dev environment by starting Metro (`CI=1 npx expo start --port 8081` from `mobile/`) and requesting a bundle, e.g. `curl "http://localhost:8081/node_modules/expo-router/entry.bundle?platform=android&dev=true"` (expect HTTP 200, a multi-MB JS bundle).
- OpenAI / Google Vision / MinIO are optional; the app degrades gracefully when their keys/services are absent (Turkish NLP for voice commands works locally without OpenAI).
