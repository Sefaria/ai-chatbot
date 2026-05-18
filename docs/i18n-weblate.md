# Weblate Operations for ai-chatbot

This guide documents the production localization workflow for `ai-chatbot` using Weblate on Coolify.

## Scope

- Host: `https://weblate.sefaria.org`
- Auth: Google SSO, restricted to `@sefaria.org`
- Repo: `Sefaria/ai-chatbot`
- Languages at launch: `en` (source), `he` (target)
- Delivery model: Weblate opens pull requests to `main` (no direct push to `main`)

## 1) Prerequisites

Complete these before creating the Coolify resource.

### DNS

- Create `weblate.sefaria.org` and point it to the Coolify host.
- Validate resolution before deployment:
  - `dig +short weblate.sefaria.org`

### Google OAuth client

Create an OAuth 2.0 Web Application client in Google Cloud.

- Authorized JavaScript origins:
  - `https://weblate.sefaria.org`
- Authorized redirect URIs:
  - `https://weblate.sefaria.org/accounts/complete/google-oauth2/`

Store:

- `WEBLATE_SOCIAL_AUTH_GOOGLE_OAUTH2_KEY`
- `WEBLATE_SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET`

### GitHub bot account (for PR creation)

Create a machine user (example: `sefaria-weblate`) with a fine-grained PAT scoped to `Sefaria/ai-chatbot`:

- Repository permissions:
  - `Contents: Read and write`
  - `Pull requests: Read and write`
  - `Metadata: Read-only`

Store:

- `WEBLATE_GITHUB_USERNAME`
- `WEBLATE_GITHUB_TOKEN`

### Generate secrets

Generate once and store in your secrets manager:

```bash
openssl rand -hex 32  # WEBLATE_ADMIN_PASSWORD
openssl rand -hex 32  # WEBLATE_SECRET_KEY
openssl rand -hex 32  # POSTGRES_PASSWORD
openssl rand -hex 32  # REDIS_PASSWORD
openssl rand -hex 32  # WEBLATE_GITHUB_WEBHOOK_SECRET
```

## 2) Deploy in Coolify

Use these repo assets:

- `deploy/coolify/weblate/docker-compose.yml`
- `deploy/coolify/weblate/.env.example`

### Create the resource

1. In Coolify, create a new **Docker Compose Empty** resource.
2. Paste `deploy/coolify/weblate/docker-compose.yml` into the Compose editor.
3. Add all environment variables from `.env.example` in Coolify (set secrets as secret variables).
4. Attach domain `weblate.sefaria.org` to the `weblate` service on container port `8080`.
5. Deploy.

### Expected deploy result

- `weblate`, `database`, and `cache` services all healthy.
- `https://weblate.sefaria.org` serves Weblate with a valid TLS certificate.

### CSRF failures behind Coolify proxy

If a form POST fails with `CSRF verification failed` and the reason says `Origin checking failed - https://weblate.sefaria.org does not match any trusted origins`, ensure Coolify sets:

```bash
WEBLATE_SITE_DOMAIN=weblate.sefaria.org
WEBLATE_ENABLE_HTTPS=1
WEBLATE_SECURE_PROXY_SSL_HEADER=HTTP_X_FORWARDED_PROTO,https
```

`WEBLATE_SECURE_PROXY_SSL_HEADER` is required by current Weblate Docker images behind TLS-terminating reverse proxies. The value is case-sensitive; use lowercase `https`.

### PostgreSQL password mismatch after redeploy

The official Postgres image only applies `POSTGRES_PASSWORD` when the data directory is first initialized. If the named Postgres volume already exists, changing the Coolify `POSTGRES_PASSWORD` variable does not update the stored password for the `weblate` database role.

If Weblate logs `FATAL: password authentication failed for user "weblate"` even though the variable is set, the app is probably using the new Coolify value while Postgres still has the old value in its persisted volume.

For a new/empty deployment, delete the Postgres volume for this Coolify resource and redeploy. In this compose file the unprefixed volume name is `weblate-pg-reset2`; Coolify may prefix it with the resource/project name.

For a deployment with data to preserve, open a shell in the `database` container and rotate the stored role password to match the current container environment, then restart `weblate`:

```bash
psql -U weblate -d weblate -c "ALTER USER weblate WITH PASSWORD '$POSTGRES_PASSWORD';"
```

## 3) Verify Google SSO

1. Visit `https://weblate.sefaria.org` and click **Sign in with Google**.
2. Sign in with a `@sefaria.org` account; ensure login succeeds.
3. Try a non-`@sefaria.org` Google account; ensure it is rejected.
4. Promote your account to admin from Coolify terminal:
   - `weblate createadmin --update --username your.name@sefaria.org`

## 4) Configure ai-chatbot in Weblate

In Weblate:

1. Create project: `Sefaria ai-chatbot`.
2. Add component with:
   - Source repository: `https://${WEBLATE_GITHUB_USERNAME}:${WEBLATE_GITHUB_TOKEN}@github.com/Sefaria/ai-chatbot.git`
   - Push URL: `github:Sefaria/ai-chatbot`
   - Branch: `main`
   - File mask: `src/i18n/locales/*.json`
   - Monolingual base language file: `src/i18n/locales/en.json`
   - File format: `JSON file`
   - Edit base file: `No`

Recommended add-ons/options:

- Cleanup translation files: enabled
- Squash git commits: enabled
- JSON indent: `2`
- JSON key sorting: disabled

## 5) Configure GitHub webhook

In `Sefaria/ai-chatbot` GitHub repo settings:

- Payload URL: `https://weblate.sefaria.org/hooks/github/`
- Content type: `application/json`
- Secret: `WEBLATE_GITHUB_WEBHOOK_SECRET`
- Events: at least `Push` and `Pull request`

## 6) Smoke test PR loop

1. In Weblate, edit one Hebrew string in `he.json` (one-character change is enough).
2. Save the translation.
3. Confirm Weblate opens/updates a PR to `main` from the machine user.
4. Review PR diff formatting:
   - 2-space indentation
   - no key reordering
5. Merge the PR.
6. Confirm Weblate syncs cleanly after webhook delivery.

## 7) Day-to-day translator workflow

1. Open `https://weblate.sefaria.org`.
2. Sign in with Google (`@sefaria.org`).
3. Choose `Sefaria ai-chatbot` component and translate strings.
4. Weblate prepares PRs; engineers review and merge in GitHub.

## 8) Adding a new language later

1. In Weblate component, click **Start new translation**.
2. Choose language code.
3. Translate in Weblate; PRs will follow the same workflow.

