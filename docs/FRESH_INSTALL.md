# Fresh Install Guide

Use this guide for a clean local setup. It is the recommended path for new developers.

## 1. Prerequisites

- Python `3.11+`
- Node.js `22+` and npm
- PostgreSQL `15+`
- Git

## 2. Clone and Install

```bash
git clone <repo-url>
cd ai-chatbot
```

### Backend

```bash
cd server
python3.11 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

### Frontend

```bash
cd ..
npm install
```

## 3. Configure Environment Variables

Edit `server/.env`.

Required for local development:

- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `ANTHROPIC_API_KEY`
- `CHATBOT_USER_TOKEN_SECRET`

Common optional variables:

- `BRAINTRUST_API_KEY`
- `BRAINTRUST_PROJECT`
- `CORE_PROMPT_SLUG`
- `ENVIRONMENT`

Important auth clarification:

- The request field `userId` is an encrypted token, not a plain user ID string.
- This service decrypts that token using `CHATBOT_USER_TOKEN_SECRET`.
- If another app in your stack still uses a legacy name like `CHATBOT_USER_ID_SECRET`, keep its value identical to `CHATBOT_USER_TOKEN_SECRET`.

## 4. Start PostgreSQL

Recommended: local PostgreSQL matching production behavior.

Example local DB setup:

```bash
psql postgres -c '\du'   # list existing roles
createdb chatbot         # create once
```

Then set:

```bash
DB_NAME=chatbot
DB_USER=<your-postgres-role>
DB_PASSWORD=<your-role-password-or-empty>
DB_HOST=localhost
DB_PORT=5432
```

Container fallback (if you do not want a local Postgres install):

```bash
docker compose up -d postgres
```

For Docker Compose Postgres, use:

```bash
DB_NAME=chatbot
DB_USER=chatbot
DB_PASSWORD=chatbot_password
DB_HOST=localhost
DB_PORT=5438
```

## 5. Run Migrations and Start Backend

```bash
cd server
source venv/bin/activate
python manage.py migrate
python manage.py runserver 0.0.0.0:8001
```

Health check:

```bash
curl http://localhost:8001/api/health
```

## 6. Start Frontend

In a second terminal from repo root:

```bash
npm run dev
```

Frontend dev server: `http://localhost:5173`

By default, Vite proxies `/api/*` to `http://localhost:8001`, so the demo page works with `api-base-url="/api"`.

## 7. Frontend Build for Embedded Testing

When testing the built widget (not Vite hot reload), rebuild after frontend changes:

```bash
npm run build
```

The output bundle is `dist/lc-chatbot.umd.cjs`.

## 8. Development Modes

- Local backend + local frontend (hot reload): `python manage.py runserver` and `npm run dev`.
- Local backend + built frontend bundle: run `npm run build`, then serve `dist/lc-chatbot.umd.cjs` from your host app.

This repo is split as a backend microservice (`server/`) and a micro frontend bundle (`dist/`).

## 9. Evals (Optional)

`evals/run_eval.py` also needs:

- `BRAINTRUST_API_KEY`
- `CHATBOT_USER_TOKEN` (encrypted user token for API auth)

Run:

```bash
python evals/run_eval.py --local
```

## 10. Troubleshooting

- Wrong Python interpreter: use `server/venv/bin/python` in your IDE/debugger.
- Running commands in the wrong repo: backend commands must run in `server/`.
- Migration errors: re-check DB env vars in `server/.env`, then run `python manage.py migrate`.
- `invalid_userId` or `userId_expired`: you passed a bad/expired token or mismatched `CHATBOT_USER_TOKEN_SECRET`.
- Chatbot appears disabled in host app: confirm that app's experiment/feature flags are enabled for your user.
- Frontend changes not appearing in embedded QA: run `npm run build` again.

## 11. Notes on Scripts

- `./setup.sh` and `./start.sh` still exist, but this manual flow is the source of truth for fresh installs.
- Use this guide when onboarding or debugging environment issues.
