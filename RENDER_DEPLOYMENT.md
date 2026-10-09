# Exam Formatter REV2 Render deployment

These files adapt the Render setup from your REV1 application to the REV2 web and WhatsApp service. Both interfaces run in one FastAPI/Uvicorn process and share Avnish’s formatter. This package supplies configuration and instructions; it does not deploy or change your Render account.

## Included Render files

| File | Purpose |
| --- | --- |
| `render.yaml` | Blueprint, health check, runtime environment, and persistent disk |
| `.python-version` | Python 3.12.10, matching REV1 |
| `start.sh` | Runtime data initialization and one Uvicorn process |
| `render.env.example` | Environment checklist with your existing names |
| `bootstrap.py` | First-start setup of the three bundled references |
| `RENDER_DEPLOYMENT.md` | This deployment guide |
| `requirements.txt` | REV2 Python dependencies, including FastAPI and Uvicorn |

REV2’s application target is `server:app`. Keep `startCommand: bash start.sh`. REV1’s Gunicorn target `web.app:app` and `GUNICORN_*` settings do not apply to this ASGI service.

## Public endpoints

| Purpose | Endpoint |
| --- | --- |
| Browser UI | `/` |
| Health check | `/health` |
| Meta verification | `GET /webhook` |
| WhatsApp messages and delivery updates | `POST /webhook` |

Your callback is **https://YOUR-EXISTING-RENDER-HOST/webhook** when updating the same service. `/webhooks/whatsapp` remains an alias from the first REV2 package. Use the real hostname shown in your Render dashboard; the attachment does not establish a specific deployed hostname.

## Updating your existing Render service

1. Put the contents of `exam_formatter_rev2/` in the repository root used by your existing service. Keep `.python-version`, `render.yaml`, and `start.sh` alongside `server.py`.
2. In the existing service settings, use:

   ```text
   Build Command: pip install --upgrade pip && pip install -r requirements.txt
   Start Command: bash start.sh
   Health Check Path: /health
   ```

3. Set `PYTHON_VERSION=3.12.10` or retain the matching `.python-version` file.
4. Add or retain a persistent disk at `/var/data`, and set `EXAM_DATA_ROOT=/var/data`. The included Blueprint uses a 1 GB disk. For an existing disk, retain its mount path and current size if they differ; update `EXAM_DATA_ROOT` to match. Do not shrink or replace an existing disk containing data.
5. Set the environment variables in the table below. Save secrets directly in Render rather than committing a populated `.env`.
6. Deploy the existing service. The startup script seeds the bundled Math 10, Chemistry 11, and English 10 references only if its REV2 `references/index.json` does not exist. Existing REV2 references and teacher job records stay authoritative across restarts.
7. Open `/health`, then `/`. Sign in and verify subject selection. English remains disabled until staff validate it.
8. In Meta, retain the same host and `/webhook`, verify with `VERIFY_TOKEN`, and subscribe to `messages`.
9. Run the acceptance checks in `TESTING.md` with Avnish.

Updating the existing service keeps its current hostname. Creating a separate service generates a separate host, so that host would need to be configured in Meta.

## Blueprint deployment

If using New Blueprint, place `render.yaml` in your repository root and select that repository. Enter the secret values marked `sync: false`, review the service/disk configuration, and create the deployment. The name `exam-paper-formatter` and region `virginia` come from REV1. The plan field is omitted so an existing service retains its compute plan; a new service uses Render’s default paid web-service plan. The disk configuration requires paid compute.

If the application stays in a subfolder in your repository, set the Blueprint Path to that subfolder’s `render.yaml` and add `rootDir: exam_formatter_rev2` to its service definition. For a manual service, set its Root Directory to the same folder. Adjust this value to your actual folder name.

## Environment variables

| Name | Value or purpose |
| --- | --- |
| `APP_ENV` | `prod` |
| `PYTHON_VERSION` | `3.12.10` |
| `OPENAI_API_KEY` | Your API key |
| `EXAM_REDO_MODEL` | `gpt-6-astra`, the supplied REV2 model; must be available to your account |
| `OPENAI_REASONING_EFFORT` | `high` |
| `VERIFY_TOKEN` | Your chosen Meta webhook verification token |
| `WHATSAPP_TOKEN` | Your Meta Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | The business phone-number ID |
| `WHATSAPP_GRAPH_VERSION` | `v25.0` from REV1, configurable to the version supported by your app |
| `WHATSAPP_APP_SECRET` | Required for signed incoming POST requests |
| `WHATSAPP_ASYNC` | `true` in production |
| `WHATSAPP_HTTP_TIMEOUT` | `60` seconds |
| `EXAM_DATA_ROOT` | `/var/data`, matching the disk mount |
| `LOG_LEVEL` | `INFO` |
| `PYTHONUNBUFFERED` | `1` |
| `ADMIN_PASSWORD` | A strong administrator password |
| `WEB_TEACHER_ACCESS_CODE` | The shared beta teacher access code |
| `COOKIE_SECURE` | `true` for HTTPS |
| `MAX_UPLOAD_MB` | `15` |
| `SESSION_MINUTES` | `30` |
| `RETENTION_DAYS` | `7` |
| `WHATSAPP_ALLOWED_SENDERS` | Optional comma-separated international numbers without `+` |
| `PORT` | Render supplies it; `start.sh` reads it automatically |

The WhatsApp/OpenAI/data variable names match REV1. The additional authentication, reasoning, and retention settings belong to REV2. The `render.env.example` file is a checklist; Render does not automatically import it.

## Storage and startup

The disk stores `references/`, `jobs/`, `pending/`, `service.sqlite3`, and activity/usage records. Only files beneath the mounted path are durable. The startup script initializes references after Render mounts the disk; the build command does not access that disk.

The older single-reference `active.docx`/`active.json` format is not a REV2 index. When an old data folder has no `references/index.json`, startup adds the bundled REV2 references and index while retaining old files. REV1 exam history/session records are not imported into the new SQLite service.

Keep one application process and one instance. Job processing, incoming menus, and outgoing delivery use background threads within it. A known OpenAI response is resumed after restart; unresolved submissions are held for administrator review. Persistent disks prevent loss of that recovery metadata across deploys. The Free plan cannot attach this persistent disk; an ephemeral test would lose its durability guarantees.

## Deployment verification

- `/health` returns `{"status":"ok"}`.
- Teacher and administrator sign-in work over HTTPS.
- Math and Chemistry are enabled; English is visibly unavailable pending validation.
- Hi returns the introduction and main menu through WhatsApp.
- Real DOCX upload, confirmation, result delivery, and error handling pass independently for each validated subject.
- Reference changes and completed job history survive a service restart.
- Logs show sanitized job IDs and operational failures, not exam text or API secrets.

Mock tests and startup checks in this package do not verify your Render account, real model access, Meta permissions, or final academic formatting quality.

## Official Render references

- [Blueprint YAML reference](https://render.com/docs/blueprint-spec)
- [Persistent disks](https://render.com/docs/disks)
- [Python version configuration](https://render.com/docs/python-version)
