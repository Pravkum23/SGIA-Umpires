# WhatsApp availability summary

The report covers SHL5 Tuesday–Friday on Monday at 14:00 Singapore time, and
Saturday–Sunday on Thursday at 21:00 Singapore time.

It lists people available for at least one selected match, people with no
availability selected, and each match's available umpires, scorers and coverage.
"No availability selected" includes people who submitted zero selections and
people who have not responded. The current cycle-level audit cannot reliably
distinguish those responses for each date batch.

Delivery uses Meta WhatsApp Cloud API. A personal WhatsApp account can receive
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
