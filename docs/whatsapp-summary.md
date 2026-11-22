# WhatsApp availability summary

The report covers SHL5 Tuesday–Friday on Monday at 14:00 Singapore time, and
Saturday–Sunday on Thursday at 21:00 Singapore time.

It lists people available for at least one selected match, people with no
availability selected, and each match's available umpires, scorers and coverage.
"No availability selected" includes people who submitted zero selections and
people who have not responded. The current cycle-level audit cannot reliably
distinguish those responses for each date batch.

Delivery supports Meta WhatsApp Cloud API and self-hosted WAHA.

## Meta setup

The default provider is Meta. A personal WhatsApp account can receive
the messages. A registered Business Platform sender is required to send them.

Before enabling:
1. Register a sender and obtain its phone-number ID and messaging access token.
2. Create and approve a body-only utility template, using positional parameter
   1 for the report. Example body:
   "Your requested SGIA availability summary is ready: {{1}}. Open SGIA Umpires to review the responses."
   Supply a realistic report as the parameter example during template approval.
3. Store repository Actions secrets WHATSAPP_ACCESS_TOKEN,
   WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_TO (recipient with country code).
   The existing DATABASE_URL secret is reused.
4. Set repository variables WHATSAPP_TEMPLATE to the approved name and
   WHATSAPP_LANGUAGE to its language code (default en_US).
5. After recipient consent and sender setup, set SGIA_WHATSAPP_ENABLED=true.
6. Run the workflow manually and confirm receipt on the recipient's phone.

Until enabled, the workflow skips delivery. It writes no report contents to logs.
The delivery table prevents duplicate sends for the same weekly period.
ACCEPTED means the provider accepted the message, not proof of delivery.
PENDING after an error requires checking provider delivery before an operator
removes the reservation to retry. Automatic retries do not resend it.

## Free WAHA Core option

[WAHA](https://github.com/devlikeapro/waha) provides a free Core edition.
No Meta sender or approved template is needed for this transport. It is an
unofficial WhatsApp client, so account blocking remains possible; use a dedicated
sender number. Software is free; hosting is only free if you already have a
suitable machine or hosting allowance. GitHub Actions schedules the report but
does not host a persistent WhatsApp session.

1. Install WAHA Core on a persistent server using its
   [quick start](https://waha.devlike.pro/docs/overview/quick-start/).
   Preserve its session storage across restarts. Protect the dashboard and API
   with authentication and expose the API only through HTTPS.
2. Create/start the `default` session and scan its QR using WhatsApp Linked Devices.
   Check the session is WORKING. Keep the session credentials private.
3. Add Actions secrets WAHA_URL (HTTPS base URL), WAHA_API_KEY and WHATSAPP_TO
   (your receiving number, country code included). DATABASE_URL is already set.
4. Set repository variable WHATSAPP_PROVIDER=waha. WAHA_SESSION defaults to
   default. Meta secrets and template variables are not required in this mode.
5. Set SGIA_WHATSAPP_ENABLED=true only after setup. Run the summary workflow
   manually and verify receipt. Monday 14:00 and Thursday 21:00 Singapore
   schedules then use WAHA. GitHub scheduled jobs may run late.

The implementation is prepared, not connected: a host, paired WhatsApp session,
API credentials and receiving number are still required. Provider changes do not
bypass duplicate protection. An ambiguous failed send stays PENDING for review.
CallMeBot was also checked on 22 November 2026; its free service reported full
capacity and was not selected.
