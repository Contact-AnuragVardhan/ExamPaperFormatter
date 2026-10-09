# Validation performed for this package

Latest automated validation date: October 9, 2026 (UTC).

- **71 automated tests passed** using pytest on Python 3.12. Tests cover all configured subjects with independent references, Word-package validation, menu navigation, the global Hi reset, repeated confirmation, webhook signatures and deduplication, output classification, exact output bytes, owner/admin permissions, reference management, safe cancellation/retry, delivery retry/callbacks, retention, timeout confirmation, incomplete/missing output, lost response acknowledgement, response-ID recovery, and cached result recovery after remote container cleanup.
- **Earlier browser smoke test passed** in headless Chromium on October 8: teacher login, subject selection, DOCX upload, confirmation, asynchronous mocked completion, download, history, help, logout, administrator login, and all three reference entries.
- **Downloaded bytes matched the mock engine result exactly.** No JavaScript runtime errors occurred.
- **Desktop and mobile screenshots were inspected.** At 390px mobile width there was no horizontal page overflow.
- JavaScript syntax and Python imports/compilation were checked.

OpenAI and Meta transport behavior was mocked during integration tests. No live API credits were spent and no real WhatsApp messages were sent. No claim is made that the three subjects’ actual generated exam layouts or academic content preservation have passed live acceptance. In particular, English remains disabled pending resolution and staff validation of the issue identified in the supplied guide.

Hosting, HTTPS credentials, Meta account permissions, real media delivery, model access, and staff acceptance must be checked in your configured environment. Follow `TESTING.md` with Avnish before the staff demonstration.

## Compatibility update

The primary WhatsApp route now matches the attached application: GET and POST `/webhook`. The prior `/webhooks/whatsapp` route remains an alias. Compatibility checks exercise existing environment-name precedence, previous REV2 aliases, Graph-version/HTTP-timeout defaults, model-name selection, signed requests to both routes, local synchronous menu handling, deduplication, and the production asynchronous requirement. Existing web functionality and Hi navigation are retained.

## Render files update

Added the REV1-derived Blueprint, Python version pin, and deployment guide. Startup now seeds references after the persistent disk is mounted. Two additional tests verify initialization on an empty disk with legacy files and preservation of staff reference changes/deletions on restart. YAML parsing and shell syntax were checked locally. No Render deployment was performed.

## Error diagnostics update

Corrected the uncertainty classification for definitive response-submission
rejections. Added sanitized administrator diagnostics, processing-stage tracking,
and a database migration preserving existing request history. Added 21 test cases
covering rejection statuses, ambiguous submission statuses, polling/upload errors,
credential redaction, administrator-only visibility, older database migration,
reconciliation confirmation, and unexpected worker failures.

The October 9 checks also verified JavaScript syntax, HTML escaping and diagnostic
visibility in the administrator job renderer, Python compilation, and shell
syntax. The full browser smoke test was not repeated for this update. No live
OpenAI or Meta calls were made. The underlying cause of the reported historical
request cannot be established from its public JSON because the earlier version
discarded the exception detail.
