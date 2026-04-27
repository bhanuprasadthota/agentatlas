# Deploy AgentAtlas API on Render

Estimated time: **10 minutes**.

## Prerequisites

- [Render account](https://render.com) (free tier works)
- [Supabase project](https://supabase.com) with `setup.sql` applied
- OpenAI API key

## Step 1 — Apply Supabase migrations

Open your Supabase project → SQL Editor → paste and run [`supabase/setup.sql`](../supabase/setup.sql).

## Step 2 — Create a Web Service on Render

1. Go to [render.com/dashboard](https://dashboard.render.com) → **New → Web Service**
2. Connect your GitHub account and select `bhanuprasadthota/agentatlas`
3. Render auto-detects `render.yaml` — confirm the settings:
   - **Runtime**: Docker
   - **Branch**: `main`
   - **Plan**: Starter ($7/mo) or Free (spins down after inactivity)

## Step 3 — Set environment variables

In the Render dashboard → your service → **Environment** tab, add:

| Key | Value |
|-----|-------|
| `SUPABASE_URL` | `https://your-project.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | your service role key |
| `OPENAI_API_KEY` | your OpenAI key |
| `AGENTATLAS_API_KEY` | any strong random string (this protects your API) |

Generate a secure API key:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Step 4 — Deploy

Click **Deploy**. First build takes ~5 minutes (Playwright installs Chromium).

Once live, your API is at:
```
https://agentatlas-api.onrender.com
```

## Step 5 — Test the live API

```bash
curl -s https://agentatlas-api.onrender.com/health
# {"status":"ok"}

curl -s -X POST https://agentatlas-api.onrender.com/v1/schema/resolve \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{"site":"example.com","url":"https://example.com/"}'
```

## Step 6 — Wire up GitHub Actions auto-deploy

1. In Render → your service → **Settings** → **Deploy Hook** → copy the URL
2. In GitHub → repo → **Settings → Secrets and variables → Actions** → add:
   - `RENDER_DEPLOY_HOOK_URL` = the URL from step 1

Every push to `main` now triggers a Render deploy automatically.

## Step 7 — Add benchmark secrets (optional)

For weekly automated benchmarks, also add to GitHub secrets:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `OPENAI_API_KEY`

## Fly.io alternative

```bash
fly launch --copy-config --ha=false
fly secrets set SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... OPENAI_API_KEY=... AGENTATLAS_API_KEY=...
fly deploy
```

TLS and a `*.fly.dev` hostname are automatic.

## Custom domain

Both Render and Fly support custom domains in their dashboards. Point your domain's CNAME to the platform hostname and TLS is provisioned automatically.
