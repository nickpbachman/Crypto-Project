# What's Happening? — V21 Cloud Alerts

V21 turns the anomaly engine into a cloud-ready monitoring service.

## What changed
- Server-side Top 100 scanner runs every 5 minutes while the service is deployed.
- Multi-signal anomaly scoring runs independently of a user's browser.
- Major anomaly events are persisted in SQLite and exposed through `/api/alerts/recent`.
- Web Push subscriptions are stored server-side.
- Push notifications are sent through VAPID when `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, and `VAPID_SUBJECT` are configured.
- Push notification clicks open the relevant asset investigation page.
- PWA manifest + service worker added so supported mobile browsers can receive background notifications.
- `/api/alerts/scan-now` allows an immediate test scan.
- Existing in-app alerts remain available as a local fallback.

## Important deployment requirement
A local ZIP/HTML file cannot monitor the market 24/7 after the app is closed. V21 is **deployment-ready**, but the server must be hosted over HTTPS for background Web Push.

### VAPID keys
Generate a VAPID key pair with a standard Web Push/VAPID tool or library, then set:
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `VAPID_SUBJECT` (for example `mailto:alerts@yourdomain.com`)

Do not commit private keys to the repository.

### Suggested hosting
The included `render.yaml` is configured for a Docker web service. Any comparable HTTPS container host works as long as it keeps the service running continuously and persists the SQLite volume.

For production, move alert history/subscriptions to Postgres and use a persistent volume or managed database rather than ephemeral container storage.
