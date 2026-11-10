# Deploy MeetPoint for free

Three free services, all deploying straight from this GitHub repo:

```
Browser ──► Vercel (website, src/frontend)
               │  /api/*  is forwarded to ──►  Render (API, src/backend, Docker)
                                                   │
                                                   └──►  Neon (PostgreSQL)
```

Because Vercel forwards `/api` to Render, the browser only ever talks to one address, so there's no cross-site (CORS) setup.

Expect 15–20 minutes the first time. You need a GitHub account with access to this repo.

---

## 1. Database: Neon (about 3 minutes)

1. Sign up at **https://neon.tech** with GitHub.
2. Create a project:
   - **Name:** `meetpoint`
   - **Postgres version:** 16
   - **Region:** pick the one closest to your Render region (for Singapore, *AWS Asia Pacific (Singapore)*).
3. On the project dashboard, open **Connect** and copy the **connection string**. It looks like:
   ```
   postgresql://neondb_owner:xxxxx@ep-something-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
   ```
   Keep it private: it contains the database password.

The API creates its tables on first start, so there's nothing else to set up.

## 2. API: Render (about 5 minutes, plus a few minutes of building)

1. Sign up at **https://render.com** with GitHub.
2. **New → Blueprint**, pick the `Meetpoint` repo, and confirm. Render reads [`render.yaml`](../render.yaml) and proposes a free web service called **meetpoint-api**.
3. Render asks for the values marked secret:
   - **`DATABASE_URL`:** paste the Neon connection string. It works as copied; the app adds the right driver itself.
   - **`GOOGLE_MAPS_API_KEY`:** leave empty for now (see step 5) or paste your key.
   - **`CORS_ORIGINS`:** leave empty for now. You'll set it after step 3.
4. Click **Apply**. The first build takes a few minutes.
5. When it shows **Live**, open `https://<your-service>.onrender.com/health`. You should see:
   ```json
   {"ok": true, "maps_configured": false, "search": "photon"}
   ```
6. **Note the exact address** at the top of the service page. If it isn't `https://meetpoint-api.onrender.com` (Render adds a suffix when the name is taken), edit the `destination` in [`src/frontend/vercel.json`](../src/frontend/vercel.json) to match, then commit and push.

## 3. Website: Vercel (about 3 minutes)

1. Sign up at **https://vercel.com** with GitHub.
2. **Add New → Project**, then import the `Meetpoint` repo.
3. Before deploying, set:
   - **Root Directory:** `src/frontend` (click *Edit*). This setting is required.
   - **Framework preset:** Vite (detected automatically from `vercel.json`).
   - **Environment variable:** `VITE_SITE_URL` = `https://<project-name>.vercel.app`, the address Vercel shows for the project. It turns on the canonical link, `sitemap.xml` and the absolute share-image URL. If you don't know the address yet, deploy first, then add it and redeploy.
4. Click **Deploy**.
5. Back in Render, set **`CORS_ORIGINS`** to your Vercel address (for example `https://meetpoint.vercel.app`) and save. Render redeploys by itself.

Open your Vercel address. You have a public MeetPoint link to share.

## 4. Check it works

- **Website:** `https://<your-site>.vercel.app` shows the map and the form.
- **API through Vercel:** `https://<your-site>.vercel.app/api/health` returns the same JSON as in step 2.5.
- **Full flow:** add two people with search, then press **Find meeting point**. Without a Google key you get real cafés, restaurants, bars and pubs from OpenStreetMap (names and addresses, no ratings or photos), with road routes drawn on the map.
- **Location:** `https://<your-site>.vercel.app/api/locate` shows whether the server can tell your city (see *Limits* below).

## 5. Turn on real venues, photos and live travel times (Google)

1. In **https://console.cloud.google.com**, create a project and add a billing account. Google gives a monthly free credit; small projects usually stay within it.
2. Enable **Routes API**, **Places API (New)** and **Geocoding API**.
3. **Credentials → Create API key**. Restrict it to those three APIs.
4. **Billing → Budgets & alerts:** add a small budget (for example US$5) with email alerts.
5. In Render, set **`GOOGLE_MAPS_API_KEY`** and save.

The API switches to Google for search, venues, photos, travel times and journey paths by itself. The OpenStreetMap settings are then no longer used.

## Every later change

Push to `main` on GitHub. Vercel and Render both redeploy automatically. CI runs the tests on every push.

---

## Limits of the free plans

| What | What happens | What to do |
|---|---|---|
| Render free API sleeps after ~15 minutes without visits | The first visit after a break waits ~30–60 s. The site shows "Waking up the server…". | Optional: a free uptime monitor (for example UptimeRobot) that opens `/health` every 10 minutes keeps it awake. One always-on free service fits in Render's monthly free hours. |
| Neon free storage (about 0.5 GB) | Plenty: groups are deleted after 30 days. | — |
| Vercel Hobby plan | Personal, non-commercial use only. | Upgrade if MeetPoint becomes a business. |
| Public OpenStreetMap search, venues and routing (Photon, OSRM) | Fair-use servers, used only while there's no Google key. A search makes about 5 Photon requests; if Photon is busy, the site shows the fairest areas instead of venues. | Add a Google key, or self-host Photon and OSRM, before real traffic. |
| "Opens on your city" from the IP address | Needs location headers from the hosting edge, or a GeoIP database file. Vercel may not pass its location headers through the `/api` forwarding; check `/api/locate`. | Either put the site behind Cloudflare and turn on *visitor location headers*, or add a free MaxMind GeoLite2-City file and set `GEOIP_DB_PATH`. Without either, the map starts on the world view and search isn't ranked by location until the first person is added. |
| Free tier terms | Providers change their free plans from time to time. | Check each site's current pricing page. |

## Alternatives

- **Google Cloud Run** instead of Render for the API. It wakes in 1–2 s instead of 30–60 s, and the free tier is about 2 million requests a month. You'll have Google billing set up for Maps anyway. Deploy `src/backend` with its Dockerfile; Cloud Run sets `$PORT`, which the image already uses.
- **Everything in one place with Docker:** `src/docker-compose.yml` runs PostgreSQL, the API and an nginx web server on any VPS.
