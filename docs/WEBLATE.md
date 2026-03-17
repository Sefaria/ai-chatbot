# Weblate Translation Setup

Translations are managed via Weblate. No GitHub Actions are used—Weblate pulls on push (webhook) and pushes back via deploy key.

## Workflow

1. Developer pushes `src/locales/en.json` → GitHub sends push webhook to Weblate
2. Weblate pulls new source strings automatically
3. Translator works in Weblate UI
4. On save/approve → Weblate commits `he.json` directly back to the repo
5. Next build bundles updated translations

## Weblate Component Configuration

Create a **Component** in your Weblate project with:

| Setting | Value |
|---------|-------|
| Version control system | GitHub |
| Repository URL | `https://github.com/{owner}/{repo}` |
| Source language | English |
| File format | JSON file |
| File mask | `src/locales/*.json` |
| Monolingual base | `src/locales/en.json` |
| Push branch | `main` |

Enable **"Push on commit"** so Weblate pushes back automatically after a translator saves.

## GitHub Configuration

### 1. Deploy Key (Settings > Deploy keys)

Add Weblate's SSH public key with **write access** so it can push `he.json` back to the repo.

### 2. Webhook (Settings > Webhooks)

| Setting | Value |
|---------|-------|
| Payload URL | `https://hosted.weblate.org/hooks/github/` |
| Content type | `application/json` |
| Event | `push` |

## Secrets

| What | Where |
|------|-------|
| Weblate SSH public key | GitHub repo deploy key (write access) |
| `WEBLATE_API_TOKEN` | Only needed if you add optional CI steps (e.g. lock component during deploy) |

No equivalent to Crowdin's `CROWDIN_PROJECT_ID` or `CROWDIN_PERSONAL_TOKEN`—Weblate authenticates via SSH for the core sync.

## Optional: Lock Translations During CI

To prevent translator conflicts while a deploy is in progress, add to your existing CI:

```yaml
- name: Lock Weblate component
  run: |
    curl -X POST \
      -H "Authorization: Token ${{ secrets.WEBLATE_API_TOKEN }}" \
      https://hosted.weblate.org/api/components/{project}/{component}/lock/
```

For ~50 strings this is usually unnecessary.
