# Deploying BATS

BATS has two halves that deploy to different places:

| Half | Where | Why |
|---|---|---|
| Web pages (`frontend/`: `index.html`, `hms.html`) | **Vercel** | static files |
| API + PostgreSQL (`backend/`) | **Render** (`render.yaml`) | needs an always-on process, background jobs and a real database; Vercel serverless can't run these |

Vercel forwards `/api/*`, `/mock-hms/*` and `/health` to the Render API (see `vercel.json`), so the browser
only ever talks to one address and no CORS is needed.

## 1. Backend on Render

1. Sign in at https://render.com with GitHub.
2. **New > Blueprint**, pick this repo. Render reads `render.yaml` and creates the API and the database.
3. Wait for the first deploy to go green. It runs `python -m app.deploy_init`: creates the schema, tries to
   create the limited `bats_app` database role, and loads the demo data with **your generated passwords**.
4. Open the service's **Environment** tab and copy these values (Render generated them):
   - `SEED_PASSWORD_NURSE` (login `staff_meera`), `SEED_PASSWORD_TECHNICIAN` (`staff_rohit`), `SEED_PASSWORD_ADMIN` (`staff_admin`)
   - `MOCK_HMS_TOKEN` (needed to open the HMS page)
5. Note the service URL, like `https://bats-api-xxxx.onrender.com`.

Check the **Logs**: you should see either `bats_app role ready` or a warning that the app runs as the database
owner. Both work; the first is the stronger setup. Some hosts don't allow the owner to manage roles.

## 2. Pages on Vercel

1. In `vercel.json`, replace every `BACKEND_HOST` with the Render host (no `https://`), e.g. `bats-api-xxxx.onrender.com`.
2. Deploy: `vercel deploy --prod` (after `vercel login`), or import the repo at https://vercel.com/new
   (framework preset "Other"; `vercel.json` sets everything else).

## 3. After deploying

- Sign in at the Vercel URL as `staff_meera` with the nurse password from step 1.4.
- The HMS page (`/hms.html`) asks for `MOCK_HMS_TOKEN` once per browser tab.
- Check the real client IP reaches the API (needed for login rate limits): fail a login 5 times from your
  machine and read the `LOGIN_LOCKOUT` audit entry. It should name **your** IP, not a Vercel or Render one.
  If it names someone else's, change `TRUSTED_PROXY_COUNT` in `render.yaml` (default 2).

## Free-tier limits to know

- Render's free web service **sleeps after ~15 minutes idle**; the first request after that takes about a minute,
  and the background jobs (expiry sweep, HMS retry, audit check) pause while it sleeps.
- Render's free database **expires after 30 days**. Move to a paid plan before relying on the data.
- The service's disk is ephemeral, so `audit_anchor.log` resets on every restart. The audit trail's hash chain
  and append-only trigger still protect it, but the off-database anchor only helps on storage that persists.

## Safe defaults on a public deployment

The well-known demo password is refused when `APP_ENV=production`. The placeholder HMS is protected by
`MOCK_HMS_TOKEN` and `/docs` is off. Rotate any secret by changing it in Render's Environment tab and redeploying.
Never put real patient data in this prototype: it is a teaching project, not a certified medical system.
