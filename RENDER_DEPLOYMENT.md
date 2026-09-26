# ExamPaperFormatter — Render deployment

This project is a Flask web application with the existing browser UI plus the
WhatsApp Cloud API webhook.

## Public endpoints

| Purpose | Endpoint |
|---|---|
| Browser UI | `/` |
| Health check | `/health` |
| Meta webhook verification | `GET /webhook` |
| WhatsApp inbound events | `POST /webhook` |

Meta callback URL:

`https://<your-render-service>.onrender.com/webhook`

## Render files added

- `render.yaml` — Render Blueprint definition.
- `.python-version` — Python 3.12.10.
- `start.sh` — production Gunicorn startup command.
- `render.env.example` — environment-variable checklist.
- `gunicorn` added to `requirements.txt`.

The production target is `web.app:app`.

## Required Render environment variables

Set these values in Render:

```text
OPENAI_API_KEY
VERIFY_TOKEN
WHATSAPP_TOKEN
WHATSAPP_PHONE_NUMBER_ID
```

Recommended/default values from `render.yaml`:

```text
EXAM_REDO_MODEL=gpt-4.1
WHATSAPP_GRAPH_VERSION=v25.0
WHATSAPP_ASYNC=true
WHATSAPP_HTTP_TIMEOUT=60
GUNICORN_WORKERS=1
GUNICORN_THREADS=4
GUNICORN_TIMEOUT=300
```

`WHATSAPP_APP_SECRET` is optional but recommended. When configured, incoming
Meta webhook POSTs are validated with `X-Hub-Signature-256`.

## Deploy with the Blueprint

1. Put the `ExamPaperFormatter` folder in a Git repository and push it to your
   Git provider.
2. In Render choose **New > Blueprint** and select the repository containing
   `render.yaml`.
3. Enter the secret environment variables when Render asks for the values.
4. Deploy the service.
5. Open `https://<service>.onrender.com/health`. It should return:

   ```json
   {"status":"ok"}
   ```

6. Open the base Render URL in a browser and verify the existing formatter web
   page still works.
7. Make sure an active Reference Exam is available from the browser UI before
   using WhatsApp formatting.
8. In Meta WhatsApp configuration, set the callback URL to:

   `https://<service>.onrender.com/webhook`

9. Use the same value in Meta's verification-token field as the Render
   `VERIFY_TOKEN` value.
10. Subscribe the Meta app to WhatsApp `messages` events.

## Manual Render setup instead of Blueprint

If you do not use `render.yaml`, create a Python Web Service with:

```text
Build Command: pip install --upgrade pip && pip install -r requirements.txt
Start Command: bash start.sh
Health Check Path: /health
```

## Important storage limitation

The formatter currently stores its active reference, generated exams,
WhatsApp sessions, and message-deduplication information in local files under
`data/`.

Render's normal service filesystem is ephemeral. On the Free plan it is also
lost when the service spins down. Therefore:

- a newly uploaded reference can disappear after a restart/redeploy/spin-down;
- generated exam history can disappear;
- an in-progress WhatsApp conversation can disappear;
- local message-deduplication state can disappear.

For a short POC this is acceptable, but it is not durable production storage.

If you later use a Render persistent disk, mount it at `/var/data` and set:

```text
EXAM_DATA_ROOT=/var/data
```

For a multi-instance or stronger production design, move session/index/files to
PostgreSQL/object storage instead of local JSON/files.

## Why Gunicorn uses one worker

The current WhatsApp session store and web exam index are JSON/local-file based.
Multiple Gunicorn worker processes could update those files concurrently.
`start.sh` therefore defaults to one worker with four threads:

```text
GUNICORN_WORKERS=1
GUNICORN_THREADS=4
```

Do not increase the worker count until the shared state has been moved to a
proper shared store such as PostgreSQL/Redis.

## Render settings aligned with ExamHelper

The non-project-specific Render settings intentionally match ExamHelper:

```text
plan: starter
region: virginia
PYTHON_VERSION=3.12.10
WHATSAPP_GRAPH_VERSION=v25.0
VERIFY_TOKEN
WHATSAPP_TOKEN
WHATSAPP_PHONE_NUMBER_ID
```

The settings that remain different are project-specific: this application is Flask/Gunicorn rather than FastAPI/Uvicorn, uses `EXAM_REDO_MODEL=gpt-4.1`, and currently keeps formatter/session state in local files rather than PostgreSQL.
