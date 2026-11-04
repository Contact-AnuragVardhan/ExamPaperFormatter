# Testing the merged source

Install the dependencies listed in `requirements.txt` and `requirements-dev.txt`, then run `python -m pytest -q` from the project root.

The suite covers the legacy FORMAT/CHECK orchestration, WhatsApp menus and administrator actions, web endpoints, teacher registry allow/deny and revocation checks, bilingual standalone Spellcheck, Word package preservation, web/WhatsApp Spellcheck output, and retriable WhatsApp delivery of both Spellcheck attachments.

Use a temporary Teacher Helper SQLite database for local testing (see `tests/conftest.py`), or configure an accessible PostgreSQL Teacher Helper database for a deployed instance. `DATABASE_URL` is required for service startup. For acceptance testing, log in with a registered teacher's WhatsApp number and the teacher access code; administrators log in with `ADMIN_PASSWORD`.

The automated suite uses a fake OpenAI client, so it does not verify a live provider account or a real WhatsApp webhook. Verify live English and Hindi files through both interfaces separately. For each, intentionally include two known spelling errors, check that the corrected DOCX contains both fixes, and inspect the TXT changes report. Check that original formatting and reference-based FORMAT/CHECK behavior are unchanged.

## Revision 2.2 checks

`tests/test_release_22.py` tests direct WhatsApp `SPELLCHECK`, menu-based Spellcheck, and Web Spellcheck with exactly two English or Hindi misspellings per document. It checks both outputs, Word package preservation, compact admin activity, registered-teacher restrictions, and restart recovery (including interrupted document sends). These are simulated WhatsApp deliveries, not live Meta tests. For live acceptance, send the same four scenarios through a registered WhatsApp number and the deployed Web UI.

Before a deployment install `requirements.txt` (including the pinned `openai` and PostgreSQL driver) and run `pytest -q`. The Meta and OpenAI credentials must be verified separately in the deployment environment.
