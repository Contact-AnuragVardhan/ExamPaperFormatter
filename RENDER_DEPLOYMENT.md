# Render deployment — Revision 2.2

This source package combines the GitHub `main` teacher-registration controls with the new standalone Spellcheck operation. Neither the GitHub repository nor the Render service has been modified by this package creation.

## Setup

1. Connect the source repository to a Render web service using the supplied `render.yaml` and `start.sh`.
2. Configure `OPENAI_API_KEY`, `ADMIN_PASSWORD`, `WEB_TEACHER_ACCESS_CODE`, `DATABASE_URL`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `VERIFY_TOKEN` and `WHATSAPP_APP_SECRET` in the service environment. `DATABASE_URL` must point to the existing Teacher Helper database containing `teacher_profile`.
3. Set `WHATSAPP_VERIFY_SIGNATURE=true` and `WHATSAPP_ASYNC=true` for production. Use the proper HTTPS deployment origin and `COOKIE_SECURE=true`.
4. Preserve the existing persistent `EXAM_DATA_ROOT` disk and reference documents; do not deploy ZIP copies of runtime SQLite, uploaded teacher exams, logs or old jobs.
5. Verify `/health`, web administrator sign-in, teacher registry validation, web TESTING SC and WhatsApp webhook flow using a registered test teacher.
6. Confirm both TESTING SC attachments (`*_Spellchecked.docx` and `*_Spelling_Changes.txt`) arrive and that FORMAT/CHECK still work.

`WHATSAPP_GRAPH_VERSION` is the Meta API version and must not be changed merely to bump the Exam Formatter release label. Revision 2.2 appears in the WhatsApp welcome and Web interface. An interrupted WhatsApp document send with no confirmed Meta message ID is held for manual delivery retry to avoid unintentional duplicate sends; check Meta delivery before retrying such an ambiguous send.

## Test before deployment

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

Tests mock OpenAI API operations: live API and live Meta WhatsApp delivery require separate verification in the configured environment.

## Persistent disk safety

The service stores its mutable files under `EXAM_DATA_ROOT=/var/data`; this includes
`service.sqlite3`, `references/index.json`, subject DOCX references, uploaded and
generated job documents, pending WhatsApp files, and usage/activity logs. Only
`data/references/` in the source package is a first-install seed.

At startup, `bootstrap.py` checks that production Render storage is actually mounted
and writable before it creates or opens any data files. It also checks every file
listed in the reference index. Missing or corrupted references must be restored
from a backup instead of silently overwriting an existing index. The same storage
check runs if the service is instantiated without the usual startup script.

The disk is available at runtime, not during the build command. Do not delete the
Render disk when redeploying, and back up `/var/data` independently.
