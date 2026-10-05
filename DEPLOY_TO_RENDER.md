# What's Happening? V21 — Render Deployment

## What to deploy
Deploy the **entire folder** as one Render Docker web service. Do not deploy only `web/index.html` to Netlify for this version.

The FastAPI backend serves the frontend at `/` and the API at `/api/*`, so search, scan, investigation, alert history, and cloud scanning all stay on the same HTTPS origin.

## Render steps
1. Create a GitHub repository and upload the contents of this folder to it.
2. In Render, choose **New → Blueprint** and connect the repository.
3. Render will read `render.yaml` and create the `whats-happening` Docker web service.
4. Wait for the deploy to finish.
5. Open the Render URL in Safari.
6. Confirm the URL shows the What's Happening? app.
7. Test `SEARCH` with `XRP`.
8. Test `SCAN TOP 100`.
9. Test `Enable anomaly alerts`.
10. Confirm `/api/health` returns `{"ok":true,...}` if you open it directly.

## Push notifications
Search and scanning work without VAPID keys. VAPID keys are only needed for true background Web Push notifications.

Generate a VAPID key pair locally with a Web Push/VAPID generator or Python tooling. Then add these Render environment variables:

- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `VAPID_SUBJECT` (for example `mailto:alerts@yourdomain.com`)

Never commit the private key to GitHub.

## Important
The included Render service is intentionally configured as an always-on service (`starter`) because the anomaly worker runs server-side every five minutes. A free/sleeping service is not appropriate for reliable 24/7 monitoring.

For the first deployment, SQLite is acceptable for testing. For production, move alert history and push subscriptions to Postgres and add persistent storage.
