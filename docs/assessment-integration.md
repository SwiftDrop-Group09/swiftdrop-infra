# Assessment integration smoke test

Run these commands against the isolated local assessment project. The harness
uses the loopback Compose web service and creates new synthetic records. It
briefly stops the assessment broker and restores it in cleanup.

```sh
sh development/assessment.sh up
python -B scripts/test-assessment.py --output assessment-local.json
python -B scripts/test-assessment.py --webhook-site --output assessment-webhook.json
```

The first run verifies direct booking, owner receipt access, merchant approval,
scoped and idempotent submission, dispatcher assignment, courier transitions,
proof, broker recovery, public tracking, and customer notifications. The JSON
marks this run as local-only.

The second run also creates a temporary public HTTPS Webhook.site receiver for a
synthetic merchant, matches the automatic Delivered event to the outbox event,
and verifies the signature against the original raw capture. It deletes the
receiver on exit. If the provider requires an API key, set
`SWIFTDROP_WEBHOOK_SITE_API_KEY` privately. The receiver API status is reported
separately from application failures. Keep credentials and raw payloads out of
recorded evidence. The output contains outcomes and bounded failure summaries.

[Token API](https://docs.webhook.site/api/tokens.html) ·
[Request capture API](https://docs.webhook.site/api/requests.html)
