# Joint acceptance checklist for Anurag and Avnish

Run these with real teacher exam files. Do not use an output-only DOCX-open check as proof that content/layout is correct. Compare questions, figures, wording, language, answers, and layout against the original teacher file and the selected reference.

## Subject validation

| Subject | Automated adapter coverage | Live acceptance required |
| --- | --- | --- |
| Math Class 10 | Submission, pinned reference, engine orchestration, exact delivery bytes | Check and format normal exam; layout review; missing-content report |
| Chemistry Class 11 | Separate subject/reference and independent job | Check and format normal exam; preserve chemistry notation/figures; report review |
| English Class 10 | Stored reference, default disablement, admin enablement and isolated request | Resolve the structural issue in the supplied guide; check and format representative exams; enable only after staff approval |

## WhatsApp acceptance

- [ ] Configure Meta test sender, app credentials, and supported Graph API version. For beta, set `WHATSAPP_VERIFY_SIGNATURE=false`; for signed webhooks, set it to true and supply the app secret.
- [ ] Verify GET token matching in both modes; verify unsigned POST acceptance only in beta mode and invalid-signature rejection in signed mode.
- [ ] Send Hi on first contact: friendly introduction and main menu appear.
- [ ] Send Hi during subject selection, upload, confirmation, help, processing, result, and error: returns to main menu.
- [ ] Hi during processing preserves the active job; selecting another exam does not create another paid request.
- [ ] Format and Check menus select only enabled subject/class references.
- [ ] Upload normal DOCX; confirm the filename and class before submitting.
- [ ] Change subject retains the pending exam; Replace File clears the pending upload; a newer valid attachment before confirmation replaces the earlier file.
- [ ] Invalid option keeps the appropriate state. Back, Cancel, and Done use their shown meanings.
- [ ] Queue cancellation succeeds; in-progress cancellation honestly explains that processing has started.
- [ ] Duplicate webhook and repeated confirmation produce one formatter response.
- [ ] Normal result sends a formatted exam. Structural/formatting failure sends an issues report, not an apparently completed exam.
- [ ] Word opens the received file; hash compares exactly with the saved engine result.
- [ ] Simulate media-download failure, missing reference, busy API, timeout, incomplete/no-output response, and delivery rejection.
- [ ] Delivery retry sends the same saved result; OpenAI response count does not increase.
- [ ] Reconnect after menu expiry; job still completes without losing its result.

## Browser acceptance

- [ ] Teacher sign-in, subject selection, DOCX browse/drag-drop, and explicit confirmation.
- [ ] Check and Format jobs each complete; status/history/downloads work.
- [ ] Another signed-in browser cannot view/download someone else’s requests.
- [ ] Teacher cannot access administrator endpoints or reference downloads.
- [ ] Administrator add/replace/view/enable/disable/delete reference all work.
- [ ] English stays unavailable before validation and becomes selectable after explicit enablement.
- [ ] Mobile width shows usable forms with no horizontal overflow.
- [ ] Admin job list, estimated cost note, activity log, and delivery retry work.

## Hosting acceptance

- [ ] HTTPS / secure cookies / persistent disk / one process and one replica.
- [ ] Restart with queued job: it processes once.
- [ ] Restart with saved remote response ID: polling resumes without new response creation.
- [ ] Interrupt around response creation before ID persistence: job is unresolved, no automatic paid retry.
- [ ] Restart immediately after engine result: saved result gets queued for delivery.
- [ ] Meta delivered/read callback updates state without regressing on a late sent callback.
- [ ] Seven-day cleanup removes expired closed jobs; active/unresolved jobs and outstanding deliveries remain.

Staff presentation is ready only after these live tests and subject-quality reviews succeed. Mocked test passes do not complete the joint acceptance requirement.
