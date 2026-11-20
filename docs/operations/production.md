# Running in production

WhiteHats is one Python service (API, background worker and web app) plus a database.
The Fix Factory engine is a second, optional service.

## Settings

| Variable | Purpose | Default |
| --- | --- | --- |
| `WH_DATABASE_URL` | SQLAlchemy database URL. Use Postgres in production, for example `postgresql+psycopg://wh:…@db/wh` | SQLite file in `WH_DATA_DIR` |
| `WH_DATA_DIR` | Folder for SQLite, keys, logs and exports | `backend/.data` |
| `WH_SECRET_KEY` | Master key that seals every company's key. Keep it in a secret manager. Losing it makes stored secrets unreadable | generated on first run |
| `WH_JWT_SECRET` | Signs sessions | generated on first run |
| `WH_REGION` | Region this installation stores data in (`in-south`, `in-west`, `eu-west`, `us-east`) | `in-south` |
| `WH_PUBLIC_URL` | Address users and collectors reach, used in invitation links and collector files | `http://127.0.0.1:8000` |
| `WH_OPERATOR_EMAIL`, `WH_OPERATOR_PASSWORD` | First operator account, created on first start | `operator@whitehats.example` |
| `WH_SEED_DEMO` | `0` for a clean installation without the demo company | `1` |
| `WH_SMTP_HOST`, `WH_SMTP_PORT`, `WH_SMTP_USER`, `WH_SMTP_PASSWORD`, `WH_SMTP_FROM` | Email for invitations and email alerts | not set |
| `FACTORY_URL`, `WHITEHATS_DISPATCH_TOKEN` | Fix Factory engine address and shared token | `http://127.0.0.1:8797`, not set |
| `WH_FACTORY_TENANT` | Short name of the one company the Fix Factory engine works for; no other company can see it | `acme` |
| `WH_STUDIO_HAND_EDITING` | `1` lets people draw and correct the map by hand in Clone Studio; otherwise the connections draw it and the Studio is read only | off |

## Starting

```sh
cd backend
uv sync --no-dev
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Use one worker process: the background worker (cloud log sync and retention) runs inside the API process.
Put a reverse proxy (nginx, Caddy or a cloud load balancer) in front for TLS, and allow only port 443 from outside.

Build the web app once with `cd frontend && npm ci && npx vite build`; the API serves `frontend/dist`.

## Database

SQLite is fine for a pilot.
For production use Postgres 15 or later and install its driver (`uv add "psycopg[binary]"`).
On every start WhiteHats creates missing tables and adds missing columns (see [Backups and upgrades](backups-and-upgrades.md)).

## Security checklist

- TLS everywhere; the API never listens on a public address directly.
- `WH_SECRET_KEY` and `WH_JWT_SECRET` from a secret manager, never in the repository.
- Operator accounts use two-step sign-in (enforced) and are personal, never shared.
- Restrict outbound traffic from the server to the model endpoints, the cloud log APIs and the code hosts you use.
