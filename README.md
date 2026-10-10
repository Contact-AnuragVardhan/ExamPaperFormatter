# Exam Formatter REV2 — Web and WhatsApp

This package extends Avnish’s supplied REV2 Python application with a browser interface and Meta WhatsApp Cloud API integration. Windows Tkinter, browser Check Exam / Format Exam, and WhatsApp all call the same `job.run` formatter with exactly one subject reference and the teacher’s original DOCX. `instructions.py` remains unchanged. Python handles orchestration, file validation, menus, persistence, and delivery; it does not rebuild or repair academic content.

## Start locally on Windows

Use Python 3.12 or newer. In this folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in `.env`:

```dotenv
OPENAI_API_KEY=your_api_key
EXAM_REDO_MODEL=gpt-6-astra
OPENAI_REASONING_EFFORT=high
ADMIN_PASSWORD=choose_a_strong_admin_password
WEB_TEACHER_ACCESS_CODE=choose_a_teacher_access_code
COOKIE_SECURE=false
```

Keep Avnish’s model / reasoning settings unless you deliberately validate a different configuration. The model must be available to your API account. No credentials from the uploaded ZIP are included in this package.

```powershell
python -m uvicorn server:app --host 127.0.0.1 --port 8000 --workers 1
```

Open **http://127.0.0.1:8000**. Alternatively, run `run_web.bat` after installing the dependencies. The original Windows app still runs with `python main.py` / `run.bat`.

On Linux/macOS use `python3 -m venv .venv`, `source .venv/bin/activate`, then the same pip and uvicorn commands.

## Compatibility with your attached application

The primary Meta callback remains **GET /webhook** for verification and **POST /webhook** for incoming events. Use the same public host followed by `/webhook` when deploying REV2 at your existing service. The earlier REV2 URL `/webhooks/whatsapp` remains a working alias. No public hostname was established by the attachment; it contains only deployment placeholders.

| Setting | Primary name from attached code | First REV2 name accepted as fallback |
| --- | --- | --- |
| Meta verification token | `VERIFY_TOKEN` | `WHATSAPP_VERIFY_TOKEN` |
| Meta access token | `WHATSAPP_TOKEN` | `WHATSAPP_ACCESS_TOKEN` |
| Business phone ID | `WHATSAPP_PHONE_NUMBER_ID` | Same name |
| Graph API version | `WHATSAPP_GRAPH_VERSION` | `WHATSAPP_API_VERSION` |
| App signature secret | `WHATSAPP_APP_SECRET` | Same name |
| Validate webhook signatures | `WHATSAPP_VERIFY_SIGNATURE` | New; defaults to true |
| Media/message HTTP timeout | `WHATSAPP_HTTP_TIMEOUT` | Previously hardcoded; now defaults to 60 seconds |
| Webhook asynchronous handling | `WHATSAPP_ASYNC` | Defaults to true |
| Persistent data folder | `EXAM_DATA_ROOT` | `EXAM_DATA_DIR` |
| OpenAI API key | `OPENAI_API_KEY` | Same name |
| Formatter model | `EXAM_REDO_MODEL` | `OPENAI_MODEL` |
| Runtime environment | `APP_ENV` | Defaults to dev |
| Log level | `LOG_LEVEL` | Defaults to INFO |
| Hosting port | `PORT` | Used by start.sh / container_start.py |

Primary nonempty values take precedence over their aliases. `WHATSAPP_ASYNC=false` is supported for **local development only**: incoming menus/media are handled before responding; paid formatting still runs in its background worker. Production (`APP_ENV=prod`) requires true to acknowledge webhooks promptly. `WHATSAPP_VERIFY_SIGNATURE` defaults to `true`, which requires `WHATSAPP_APP_SECRET` for signed webhook POSTs. Set `WHATSAPP_VERIFY_SIGNATURE=false` explicitly for beta operation without signature checks; the app secret may then be blank. The included Render Blueprint and Render environment example select this beta mode. Existing Render services must set the variable in their Environment settings and redeploy.

Reuse your existing variable **names**, not every old value without review. In particular, the old app selected `gpt-4.1`; REV2’s supplied model remains `gpt-6-astra`. Set `EXAM_REDO_MODEL` to the intended REV2 model. Its `EXAM_DATA_ROOT` needs the three REV2 references and `index.json`; the older `active.docx` / `active.json` reference layout is not that index.

Administrator password, teacher access code, reasoning effort, upload/session/retention limits, and optional sender allowlist are additional REV2 settings because the attached code has no matching settings. Existing `GUNICORN_*` variables are unused: REV2 is an ASGI service launched with Uvicorn and one worker. `start.sh` reuses your existing Render start filename and `PORT` / `LOG_LEVEL` values.

## Browser functionality

- Teacher / administrator sign-in with server-managed credentials and HttpOnly session cookies.
- Subject/class selection from configured references; disabled subjects are clearly unavailable.
- Check Exam and Format Exam, Word upload, drag/drop, and confirmation showing filename and subject.
- Asynchronous request progress, saved history, queued cancellation, safe manual retry, formatted-exam and issues-report downloads.
- Teacher requests are isolated by signed-in browser session. The shared teacher code is a beta access gate, not a school account directory. Signing out and signing in creates a new session and a new history; admins can access retained requests.
- Administrator reference view/download, add/replace, enable/disable, delete, all-request monitoring, delivery retry, unresolved request reconciliation, activity log, and estimated usage costs.
- Responsive desktop/mobile layout. Help explains errors, review requirements, and WhatsApp use.

The supplied references are **Math 10**, **Chemistry 11**, and **English 10**. English is disabled initially because the detailed guide explicitly identifies an unresolved structural issue. Staff must test its real output before enabling it in Administration. This package does not fix or certify that academic formatting issue.

## Connect Meta WhatsApp Cloud API

This implementation uses **Meta Cloud API**, not Twilio or WhatsApp Web automation. If your earlier project uses another provider, its webhook is not a drop-in match. This standalone service can be deployed separately or its `service` package adapted into your existing Python application.

1. Configure a WhatsApp Business Platform app and business phone number in Meta.
2. Add these server environment values (or local `.env`):

```dotenv
WHATSAPP_TOKEN=your_cloud_api_access_token
WHATSAPP_PHONE_NUMBER_ID=your_business_phone_number_id
WHATSAPP_VERIFY_SIGNATURE=true
WHATSAPP_APP_SECRET=your_meta_app_secret
VERIFY_TOKEN=choose_your_own_webhook_verification_token
WHATSAPP_GRAPH_VERSION=the_supported_version_selected_in_your_meta_app
WHATSAPP_ALLOWED_SENDERS=15551234567,919876543210
```

Use an appropriate long-lived system-user token with the messaging/media permissions required by Meta. `WHATSAPP_GRAPH_VERSION` defaults to the attached app’s `v25.0` and remains configurable. Set a version supported by your Meta app. The allowlist is optional; numbers use international format without `+`. During beta, use it to restrict who can invoke the paid formatter.

3. Deploy this service to a public HTTPS URL, using the hosting setup below.
4. Set Meta’s callback URL to **https://YOUR-HOST/webhook**. Enter the exact `VERIFY_TOKEN` chosen above.
5. Subscribe the WhatsApp app to the **messages** webhook field. This includes incoming messages and delivery statuses. Configure the business account/app subscription required by Meta.
6. Send **Hi** from an allowed test phone, then exercise subject selection, DOCX upload, confirmation, and result delivery.

The GET `/webhook` endpoint responds to Meta verification. POST requests require the configured phone-number ID. When `WHATSAPP_VERIFY_SIGNATURE=true`, they also require a valid HMAC-SHA256 `X-Hub-Signature-256`. When false, the signature header is ignored, whether absent or present, and no app secret is required. `VERIFY_TOKEN` is still required for GET verification in either mode. With the default `WHATSAPP_ASYNC=true`, webhooks only persist incoming messages and acknowledge; separate worker threads download documents, process jobs, and send replies.

Transport reference links:
- [Meta webhook setup](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint/)
- [Meta media API](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-phone-numbers/media)
- [Meta document messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/document-messages)
- [OpenAI Code Interpreter](https://developers.openai.com/api/docs/guides/tools-code-interpreter)
- [OpenAI background responses](https://developers.openai.com/api/docs/guides/background)

## WhatsApp introduction and menus

Every **Hi** (case-insensitive, with common greeting punctuation accepted) returns a friendly Exam Formatter introduction and a native WhatsApp **Open Menu** control. Teachers tap list rows or reply buttons; they do not type option numbers. Hello, Hey, Menu, and Start also return to the main menu.

| Screen | Native choices |
| --- | --- |
| Main menu | Format Exam, Check Exam, Admin, Help, Exit; Retry Delivery when a saved delivery failed |
| Subject/class | Enabled subject/class rows; Back to Main Menu; More/Previous Subjects when needed |
| Upload | Cancel, Help; attach the DOCX using WhatsApp's attachment icon |
| Confirmation | Start Formatting or Start Checking, Change Subject, Replace File, Cancel |
| Processing | Check Status, Cancel Request, Main Menu |
| Result | Format Another Exam, Check Another Exam, Help, Main Menu |
| Error | Try Again only when safe, Choose Another Subject, Upload Another File, Help, Main Menu |
| Help | Back to the previous screen, Main Menu |
| Exit | Start Again |

Menus with more choices use WhatsApp list messages; short menus use reply buttons. Subject lists are paginated to stay within Meta's row limits. Confirmation shows the subject/class and filename. Changing subject keeps the pending DOCX; replacing a file clears it, and a newer valid attachment before confirmation replaces it. PDFs, images, and corrupt files are rejected with native controls for the next action.

Selections have identifiers tied to the current menu. A stale, expired, forged, or repeated old-menu tap returns fresh controls without starting another paid request. A typed number redisplays the current controls and does not select an option. On deployment, teachers with old numbered menus should send Hi to receive the new controls.

Queued requests can be cancelled; requests already running explain that cancellation is unavailable. Check Status never starts another formatter call. Help's Back button returns to result/error menus as well as the earlier upload/confirmation screens. Hi clears pending navigation while preserving an active job, and a late result does not overwrite the current menu. Result delivery retry sends the saved document without reformatting. Interactive menu delivery failures are logged and do not mark a previously sent result document as failed.

The outgoing queue stores native interactive payloads and sends them through Meta's `type: interactive` messages API. No numeric-choice fallback is sent. Real WhatsApp account permissions and end-to-end delivery still require live acceptance with your account.

### WhatsApp document download correction

If logs show `media_lookup` HTTP 200 followed immediately by `DeliveryError`,
before `meta.media.http_response`, the earlier URL guard rejected Meta's
`lookaside.fbsbx.com` attachment host. The corrected `service/meta.py` accepts
that HTTPS host and retains the existing Facebook/fbcdn host checks. Logs now
show `meta.media.url_accepted` or `meta.media.url_rejected` with a safe host/reason,
without the signed URL or token. Unexpected origins, userinfo, nonstandard ports,
and redirect responses are rejected before credentials can reach another host.
MIME, size, checksum, and downstream DOCX structure validation remain in effect.

Deploy the updated `service/meta.py` (the new transport tests and docs are also
included), then send a fresh DOCX attachment. No environment changes are needed.
The same transport fixes teacher exam and admin reference uploads. No formatter
request is created until the download succeeds and the user confirms submission.

## WhatsApp administrator flow

Send **Hi → Open Menu → Admin**, then send the exact **ADMIN_PASSWORD** from your local `.env` or Render Environment as a text message. It is the same password used by the web and Windows administrator interfaces. The teacher access code does not grant admin access. No new environment variables or webhook endpoints are needed.

| Admin menu | Actions |
| --- | --- |
| Reference Papers | Select any stored subject/class, including disabled subjects; download/view the actual reference DOCX, replace it, enable/disable teacher access, or delete it |
| Upload New Reference | Choose subject/class from paginated lists; Other Subject/Class permits new text values; attach DOCX, review filename and availability, then Save Reference |
| Exam Requests | All, Errors/Unresolved, Active, or Failed Deliveries; select a request, refresh status, inspect diagnostics, download a saved output, cancel while queued, safely retry a failed request, retry saved delivery, or reconcile unresolved status |
| Activity Log | Recent activity, newest first; Older/Newer pages and refresh |
| Cost Totals | Today/month/all-time local estimates, with unpriced usage identified |
| System Status | Configuration readiness, actual configured model, subject availability, and request counts; no secret values |
| Admin Help / Logout | Instructions and end administrator access |

The reference list, files, enablement, jobs, activity, and costs are shared with the web interface in `EXAM_DATA_ROOT`. The original Windows UI supports add/view/replace/delete and activity/costs; it shares those records when using the same local `data/` directory. Web and WhatsApp also expose subject enablement and service job recovery. New reference pairs default to disabled. Replacing a pair retains its reference identity and configured enablement unless you explicitly toggle the confirmation's Enable/Disable After Save option. Existing active exams retain their pinned reference snapshot. Saving a reference never invokes the formatter. Concurrent reference replacements are checked before confirmation commits.

Password entry, file attachment, and optional custom subject/class names require input; every choice and navigation step uses native lists/buttons. Reference save/delete, availability changes, and job recovery actions show explicit confirmation. Download Reference sends an independent copy to the authenticated administrator; teachers cannot download the stored reference. Download Result sends the saved bytes to the administrator without changing the original teacher's delivery status. Retry Formatting may create a new charged request after confirmation; Retry Delivery only resends the saved file to the original teacher.

Admin access is bound to the sender, limited to five password attempts per five minutes, and expires after **SESSION_MINUTES** of admin inactivity (default 30). Hi, Hello, Hey, Menu, Start, Main Menu, and Logout end admin access; running teacher jobs continue. Restart/redeploy also requires a fresh WhatsApp admin login. Reserved greeting words should not be used as the admin password because they reset navigation. Outgoing notifications cannot extend admin authorization. Expired/stale buttons cannot perform admin operations, and pending privileged downloads are cancelled when access has expired. Successful passwords are removed from inbox text; login attempts are scrubbed before handling and are not printed in logs or echoed in replies. WhatsApp itself still displays the text message you send in your chat.

Unresolved requests with a saved response ID resume that existing response. Without an ID, the confirmation requires the administrator to first check the OpenAI dashboard and confirm no remote request remains active. This releases the request for a later manual retry and does not submit a paid job automatically.

For this update, commit `service/whatsapp_admin.py` and the updated `service/app.py`, `service/core.py`, `service/storage.py`, `service/whatsapp.py`, tests, and documentation. Keep `runtime_logging.py`, `service/menus.py`, and the existing Render/startup files from the earlier update. Do not commit populated `.env`, runtime SQLite, teacher uploads, job/output files, or `data/admin_downloads/`. Deploy and send Hi for fresh menus.

## Reliability and error behavior

- Inputs and a reference snapshot are stored separately under `data/jobs/<request-id>/`. The reference version is its SHA256. Reference replacement cannot change an already submitted request.
- DOCX validation checks extension, bounded byte size, ZIP integrity, required document parts, standard Word content type, and XML readability. Macro-enabled packages and excessive expanded ZIPs are rejected. This validates file structure, not perfect visual/academic quality.
- Only `PASS` + a valid result file is a completed formatting job. `OK` is a completed check with no file. `FORMAT ERROR` / `STRUCTURAL ERROR` return a clearly labeled issues report and a “Changes needed” state. Missing/invalid output and incomplete OpenAI responses never become apparently successful formatted exams.
- The original teacher file is never overwritten. Output bytes are copied without opening/re-saving the generated DOCX locally.
- SQLite persists jobs, sessions, incoming webhook IDs, an outgoing delivery queue, credentials sessions, and reference enablement. Web browser submission IDs and WhatsApp message IDs prevent repeated requests; repeated confirmation while active cannot charge twice.
- A known OpenAI background response ID is saved and resumed after restart. A crash or lost network response around the paid create call is marked **Status unresolved** and blocks another request. Administration can resume a known response; if no response ID exists, an administrator must check the OpenAI dashboard and explicitly confirm no request remains active before releasing the job.
- No automatic retry of a paid formatter POST. Explicit rate-limit rejection may be manually retried. OpenAI’s normal processing timeout is 25 minutes. Cancellation must be confirmed before timeout is treated as safely retryable.
- WhatsApp delivery uses up to three attempts with bounded delays. Uploaded result media IDs are reused. Retrying delivery sends the saved result and does not rerun the formatter. Delivery callbacks distinguish API acceptance, actual delivery, read, and failure.
- Meta does not provide an application transaction spanning “send” and the local DB update. A lost send acknowledgement can cause a duplicate document delivery; it cannot cause a second formatting request. This integration provides deduplicated processing and at-least-once outbound delivery, not a guarantee of exactly-once message display.
- Menu expiry: 30 minutes by default. Confirmed jobs survive menu expiry. Pending uploads expire; completed/failed/cancelled job files and related job records expire after seven days. Active/unresolved jobs and queued deliveries are not silently removed. Cleanup runs hourly while the service runs. Reference files are retained until explicitly deleted. Original usage/activity logs retain the desktop engine’s existing retention behavior.
- Logs contain request IDs and sanitized operational states, not exam text or API tokens. Public errors do not echo provider response content.
- Existing engine cost calculations are displayed as estimates using its shipped rates. These are not live billing data. Confirm actual charges and model access in the OpenAI dashboard.

## Hosting and persistent storage

This package deliberately uses one application process, one formatter worker, one inbox worker, and one delivery worker. Start with **`--workers 1`**, **one replica**, and no auto-reload in production. Multiple processes or replicas require a coordinated external queue; they are not supported by this SQLite implementation.

Render files are included: `render.yaml`, `.python-version`, `start.sh`, `render.env.example`, and `RENDER_DEPLOYMENT.md`. Follow `RENDER_DEPLOYMENT.md` for updating your existing service or using a Blueprint.

For Render or a similar Python host, the commands are:

```text
Build: python -m pip install -r requirements.txt
Start: bash start.sh
Health: /health
```

Use a **persistent disk** for `EXAM_DATA_ROOT`. `start.sh` initializes the bundled references on first start after the disk is mounted, and preserves existing REV2 references on later starts. Ephemeral filesystems lose sessions, requests, and references on replacement/restart; they cannot provide this service’s persistence guarantees. Supply secrets through the hosting environment, set `COOKIE_SECURE=true`, and expose only HTTPS. Keep disk/data private and backed up. No deployment is performed by this package.

A Dockerfile and `compose.yaml` are included. The container initializer copies bundled reference files into an empty mounted data volume on first start.

```bash
cp .env.example .env
# Fill the required credentials and access codes before starting.
docker compose up --build -d
```

The compose local URL is http://localhost:8000. Keep `COOKIE_SECURE=false` for that local HTTP test, and use true behind your production HTTPS proxy. Do not mount `data` as a public static directory. Do not edit reference files simultaneously from the desktop and service administrator windows.

## Test and staff acceptance

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Automated tests use fake OpenAI/Meta transports; they do not spend API credits, send real WhatsApp messages, or certify exam quality. See `TESTING.md` for the required joint live acceptance runs with Avnish. The service remains configurable code until your own credentials, HTTPS host, and Meta webhook are set up.

## Troubleshooting an unresolved request

`state: uncertain` means the service cannot safely rule out an existing remote
formatting request. It blocks another submission to avoid duplicating that call.
It does not mean the English reference or the teacher exam was judged invalid.

Sign in as **Administrator**, open **Administration**, find the request ID, and
expand **Administrator error details**. For new failures this shows the processing
stage, sanitized error message, HTTP status, and available OpenAI request/response
IDs. These details are also included in authenticated `GET /api/admin/overview`;
teacher job endpoints do not return them. The server console logs the job ID,
stage, error type, and HTTP status without credentials or exam content.

Definite rejection of the response submission (such as HTTP 400, 401, 403, or 404)
now produces `failed`, rather than `uncertain`. Correct the reported configuration
error before starting a fresh request. Lost acknowledgements, timeouts, and server
errors during submission remain unresolved until checked. Automatic paid retries
remain disabled.

For an unresolved request with a saved response ID, **Reconcile processing**
resumes that same response without submitting a new formatting request. If no
response ID was saved, first verify in your OpenAI project logs that no request
remains active, then use the reconciliation action to confirm and release it.
Do not delete the database or clear the request simply to unblock the upload.

Older failures discarded their underlying error. Installing this update cannot
reconstruct those errors, so they display that limitation explicitly. Preserve
your existing `.env` and `EXAM_DATA_ROOT` directory when replacing the code. The
database upgrade adds the diagnostics column without erasing history or enabling
English automatically. Restart the server and hard-refresh the browser afterward.

## Console logging

Use the existing `LOG_LEVEL` setting in `.env` locally and in the Render service's Environment settings. Restart or redeploy after changing it.

```dotenv
LOG_LEVEL=DEBUG
```

`INFO` (the default) prints startup/readiness, missing WhatsApp setting names, webhook validation and intake, duplicate/rate-limited messages, menu commands and state transitions, document validation failures, job queuing and state changes, OpenAI container/upload/response/output stages, processing heartbeats every 30 seconds, delivery attempts/retries/status callbacks, restart recovery, cleanup, and shutdown. `DEBUG` additionally prints every OpenAI polling cycle, successful DOCX validation, health requests, and ignored delivery callbacks. `WARNING` and `ERROR` reduce the output.

Each event has a timestamp, severity, logger, event name, and JSON metadata. Use `job_id` to follow an exam, `message_id` for an incoming WhatsApp message, `outbox_id` for a delivery attempt, and `request_id` for a web request. HTTP responses also include `X-Request-ID`. Sender/session identifiers are logged as stable hashes; uploaded filenames, exam contents, arbitrary message text, prompts, provider response bodies, passwords, and access tokens are not printed. Provider failures include stage, HTTP status, exception type, provider error code/parameter when available, and provider request/response IDs. Detailed sanitized formatter diagnostics remain available to administrators.

Example lines (illustrative IDs):

```text
INFO exam.web event=webhook.message.queued {"request_id":"request123","message_id":"wamid.example","sender_key":"abc123","message_type":"document"}
INFO exam.service event=job.queued {"job_id":"job123","channel":"whatsapp","kind":"format","subject":"math","grade":"10"}
ERROR exam.engine event=openai.request.failed {"job_id":"job123","stage":"submit_response","http_status":400,"provider_code":"unsupported_parameter","provider_param":"reasoning.effort"}
ERROR exam.service event=whatsapp.delivery.failed {"job_id":"job123","outbox_id":12,"attempt":1,"retry":true,"retry_seconds":2}
```

The server writes these events directly to standard output, so they appear in the terminal/PyCharm Run console locally and the Render service logs when hosted. No separate log viewer or log file is required. HTTP-library debug output is suppressed, and Uvicorn access logs omit query strings so webhook verification tokens are not printed.

## Project map

```text
main.py                 Original Windows interface
instructions.py         Original REV2 prompts, unchanged
job.py                  Shared engine + isolated outputs / response recovery
records.py              Original references / logs / estimated costs
server.py               ASGI entry point
service/app.py          Web API, sign-in, administration, configurable webhook signature validation
service/core.py         Shared jobs, workers, delivery, retention
service/whatsapp.py     Numbered menus and global Hi reset
service/meta.py         Meta media and messaging transport
service/storage.py      Durable SQLite records
service/files.py        Bounded DOCX validation
web/                    HTML / CSS / JavaScript browser interface
tests/                 Mocked integration and engine tests
```
