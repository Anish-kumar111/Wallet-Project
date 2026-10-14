# Wallet-Based Per-Minute Session Billing

A Django REST Framework backend for paid 1:1 consultation sessions. It provides:

- A prepaid wallet for each user.
- Mock recharges with an idempotent callback.
- A session state machine.
- Per-minute billing that ends the session automatically when the wallet runs out.

- **Design notes, state diagram and ER diagram:** [docs/DESIGN.md](docs/DESIGN.md)
- **Stack:** Python 3.11+, Django 5.1, DRF, SimpleJWT, PostgreSQL

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # then set POSTGRES_PASSWORD and a long DJANGO_SECRET_KEY
createdb -U postgres wallet_db  # or create the database any other way

python manage.py migrate
python manage.py runserver
```

All configuration comes from environment variables. You can set them in a `.env` file; see [.env.example](.env.example).

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev-only value | Also the JWT signing key. Use 32+ characters. |
| `POSTGRES_DB` / `USER` / `PASSWORD` / `HOST` / `PORT` | `wallet_db` / `postgres` / – / `localhost` / `5432` | |
| `RECHARGE_WEBHOOK_SECRET` | `dev-webhook-secret` | The value the mock gateway must send in `X-Webhook-Secret` |

## Running the tests

```bash
python manage.py test
```

The PostgreSQL user needs permission to create databases, because Django creates a temporary test database.

| Suite | What it covers |
|---|---|
| [tests/test_required_scenarios.py](tests/test_required_scenarios.py) | **The 6 required scenarios**, end to end through the HTTP API |
| [tests/test_concurrency.py](tests/test_concurrency.py) | Parallel duplicate callbacks and parallel billing runs (8 threads, real commits) |
| [consultations/test_billing.py](consultations/test_billing.py) | Billing edge cases: partial minutes, late billing runs, top-ups, final billing on end |
| [consultations/tests.py](consultations/tests.py) | State machine, permissions and start rules |
| [wallet/tests.py](wallet/tests.py) | Wallet ledger, constraints and the recharge flow |
| [accounts/tests.py](accounts/tests.py) | Registration, roles and JWT |

To run only the required scenarios: `python manage.py test tests.test_required_scenarios`

## Billing in practice

Billing is synchronous. You can trigger it in either of two ways:

```bash
# Per session, through the API (safe to call any number of times)
POST /api/sessions/{id}/bill/

# For all in-progress sessions, once (e.g. from cron every minute) or in a loop
python manage.py bill_active_sessions
python manage.py bill_active_sessions --loop 60
```

## API

Every endpoint except register, token and the recharge callback needs `Authorization: Bearer <access token>`.

### Auth

| Method | Path | Who | Body |
|---|---|---|---|
| POST | `/api/auth/register/` | anyone | `{username, password, role: "USER"\|"PROVIDER", per_minute_rate?}` (rate required for providers) |
| POST | `/api/auth/token/` | anyone | `{username, password}` returns `{access, refresh}` |
| POST | `/api/auth/token/refresh/` | anyone | `{refresh}` |
| GET / PATCH | `/api/auth/me/` | any | A provider can PATCH `per_minute_rate` |

### Wallet

| Method | Path | Who | Notes |
|---|---|---|---|
| GET | `/api/wallet/` | USER | Shows the balance |
| GET | `/api/wallet/transactions/` | USER | Ledger of all credits and debits |
| GET / POST | `/api/wallet/recharges/` | USER | POST `{amount}` creates a `pending` recharge and returns its `reference` |
| POST | `/api/wallet/recharges/callback/` | gateway | Header `X-Webhook-Secret`; body `{reference, status: "success"\|"failed"}`. Safe to retry: a duplicate returns `"applied": false` |

### Sessions

| Method | Path | Who | Transition |
|---|---|---|---|
| POST | `/api/sessions/` | USER | creates the session in `unassigned` |
| GET | `/api/sessions/`, `/api/sessions/{id}/` | participants | |
| POST | `/api/sessions/{id}/assign/` | session's user | `unassigned` → `assigned` (body `{provider_id}`) |
| POST | `/api/sessions/{id}/accept/` | assigned provider | `assigned` → `accepted_by_provider` |
| POST | `/api/sessions/{id}/start/` | session's user | `accepted_by_provider` → `in_progress` |
| POST | `/api/sessions/{id}/end/` | user or provider | `in_progress` → `completed` (runs a final billing pass first) |
| POST | `/api/sessions/{id}/cancel/` | user or provider | any state before `in_progress` → `cancelled` |
| POST | `/api/sessions/{id}/bill/` | participants | Charges the full minutes elapsed. May end the session (`ended_by: system`) |

### Errors

Business-rule violations return `{"error": "<code>", "detail": "<message>"}`:

| HTTP | `error` | When |
|---|---|---|
| 400 | `invalid_transition` | The action isn't allowed from the current state, e.g. ending a session before it starts |
| 400 | `insufficient_balance` | Starting with less than 5 × the per-minute cost |
| 400 | `active_session_exists` | The user already has an `in_progress` session |
| 400 | `session_not_in_progress` | Billing a session that isn't running |
| 400 | `invalid_provider` | Assigning to a user who isn't an active provider |
| 403 | `not_allowed` | The caller is part of the session but in the wrong role, e.g. a provider trying to start it |
| 404 | `recharge_not_found` / standard 404 | Unknown recharge reference, or a session the caller isn't part of |
| 409 | `recharge_already_finalised` | A callback that contradicts a recharge's final status (failed → success) |

### Example walkthrough (curl)

```bash
API=http://localhost:8000/api
curl -s -X POST $API/auth/register/ -H 'Content-Type: application/json' -d '{"username":"alice","password":"S3cure-pass-123"}'
curl -s -X POST $API/auth/register/ -H 'Content-Type: application/json' -d '{"username":"drbob","password":"S3cure-pass-123","role":"PROVIDER","per_minute_rate":"50"}'
U=$(curl -s -X POST $API/auth/token/ -H 'Content-Type: application/json' -d '{"username":"alice","password":"S3cure-pass-123"}' | jq -r .access)
P=$(curl -s -X POST $API/auth/token/ -H 'Content-Type: application/json' -d '{"username":"drbob","password":"S3cure-pass-123"}' | jq -r .access)

REF=$(curl -s -X POST $API/wallet/recharges/ -H "Authorization: Bearer $U" -H 'Content-Type: application/json' -d '{"amount":"500"}' | jq -r .reference)
curl -s -X POST $API/wallet/recharges/callback/ -H 'X-Webhook-Secret: dev-webhook-secret' -H 'Content-Type: application/json' -d "{\"reference\":\"$REF\",\"status\":\"success\"}"

SID=$(curl -s -X POST $API/sessions/ -H "Authorization: Bearer $U" | jq -r .id)
curl -s -X POST $API/sessions/$SID/assign/ -H "Authorization: Bearer $U" -H 'Content-Type: application/json' -d '{"provider_id":2}'
curl -s -X POST $API/sessions/$SID/accept/ -H "Authorization: Bearer $P"
curl -s -X POST $API/sessions/$SID/start/  -H "Authorization: Bearer $U"
# ...wait a couple of minutes...
curl -s -X POST $API/sessions/$SID/bill/   -H "Authorization: Bearer $U"
curl -s -X POST $API/sessions/$SID/end/    -H "Authorization: Bearer $U"
curl -s $API/wallet/ -H "Authorization: Bearer $U"
```

## Test console (React UI)

[frontend/](frontend/) is a small Vite + React + TypeScript app for exercising the backend by hand. It has three columns:

- **User console:** wallet, recharges and sessions.
- **Provider console:** rate and sessions.
- **Request log:** every API call with its status code and response body.

The two consoles log in separately, so you can drive both sides of a session from one screen.

```bash
python manage.py runserver        # backend on :8000 (DEBUG=True)
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

The Vite dev server proxies `/api` to `localhost:8000`, so no CORS setup is needed.

**Highlighted buttons:** valid session actions are highlighted. Invalid ones stay clickable, so you can watch the backend return `400 invalid_transition`.

**Dev clock:** on an in-progress session, **+1 min / +5 min** moves the session's start time back so billing can be tested without waiting. This uses `POST /api/dev/sessions/{id}/rewind/`, which is only routed when `DEBUG=True`.

**Auto-bill:** this option calls the bill endpoint on an interval.

## Project layout

```
config/          settings, URL root, DomainError → HTTP mapping (exceptions.py)
accounts/        User model with USER/PROVIDER role and provider rate, JWT auth, role permissions
wallet/          Wallet, ledger, recharge models; services.py (credit/debit/callback)
consultations/   Session model, state_machine.py, services.py (transitions), billing.py,
                 management command bill_active_sessions
tests/           required scenarios + concurrency tests
frontend/        React test console (dev tool, not part of the API)
docs/DESIGN.md   diagrams and implementation decisions
```
