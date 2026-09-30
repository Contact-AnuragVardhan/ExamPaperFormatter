CORE Phase 1 reads the fixed teacher exam, discovers one hierarchy for the whole document, then numbers that hierarchy in code.

```
python run_core.py
```

Inputs are the files in `input/`. They are read only. Outputs are `output/hierarchy.json`, `output/hierarchy.txt`, and `output/integrity.txt`.

Discovery uses one OpenAI structured call (`EXAM_REDO_MODEL`, default `gpt-4.1`) when `OPENAI_API_KEY` is set. The model returns types and parents for existing source ids. It does not rewrite question text and it does not assign final labels.

## WhatsApp layer

The Flask app now exposes a WhatsApp Cloud API webhook at `/webhook`.

- `GET /webhook` handles Meta webhook verification.
- `POST /webhook` receives WhatsApp events.
- A received Word `.docx` document is downloaded from WhatsApp.
- The bot asks, one at a time, for class/grade and subject.
- After those answers, it calls the existing formatter without changing its formatting logic.
- If formatting succeeds, the generated `.docx` is uploaded to WhatsApp and sent back to the same sender.
- `CANCEL`, `STOP`, or `RESET` clears a sender's pending conversation.

The WhatsApp workflow uses the same active reference exam as the browser UI. An administrator must upload that reference first on the web page. The metadata collected in WhatsApp is saved for the workflow/future use; the current formatting algorithm itself still uses only the teacher DOCX and the active reference DOCX.

Copy `.env.example` to `.env` and set at least:

```text
OPENAI_API_KEY=...
VERIFY_TOKEN=...
WHATSAPP_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
```

`WHATSAPP_GRAPH_VERSION` defaults to the same `v25.0` used by ExamHelper and is configurable so the Meta Graph API version is not embedded in the formatter logic. `WHATSAPP_APP_SECRET` is optional; when present, incoming POST requests are verified using `X-Hub-Signature-256`.

For local testing, `http://127.0.0.1:5000/webhook` is not reachable by Meta. Expose the Flask app through an HTTPS tunnel such as ngrok/Cloudflare Tunnel, or deploy it, and configure the resulting public HTTPS URL plus `/webhook` in the Meta app. The verification token entered in Meta must match `VERIFY_TOKEN`.

WhatsApp state is stored under `data/whatsapp/`. This JSON/thread implementation is intended for the current POC; a production deployment should use a persistent database/queue instead of local JSON files and background Flask threads.


## Render deployment

Render deployment support is included in this project. The existing browser UI
and `/webhook` WhatsApp endpoint run from the same Flask application.

Deployment files:

- `render.yaml`
- `.python-version`
- `start.sh`
- `render.env.example`
- `RENDER_DEPLOYMENT.md`

The production server starts `web.app:app` with Gunicorn. `GET /health` is the
Render health-check endpoint. See `RENDER_DEPLOYMENT.md` for the exact Render
and Meta configuration.

## Reference nonconformance corrections

Before the normal formatting pipeline runs, browser and WhatsApp uploads are checked against the active reference exam for required header roles and reference Sections A-D. If required items are missing, the formatter does not call the normal OpenAI formatting pipeline. Instead it returns `<original>_CORRECTIONS.docx`, preserving the teacher exam and inserting yellow `ERROR:` / `FIX:` paragraphs that explain what must be corrected. The teacher should make the corrections, delete the yellow notes, and upload/send the corrected DOCX again.

This behavior is covered by `tests/test_nonconformance.py` and a WhatsApp regression test in `tests/test_whatsapp.py`.
