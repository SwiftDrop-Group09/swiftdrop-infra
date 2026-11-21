# Local Kubernetes assessment

Use the local Docker Desktop Linux engine, the `k3d-swiftdrop` context, and
Python 3.11 or later. The installer imports Linux AMD64 images from the
assessment Compose build into the local k3d nodes using content-derived tags.

```sh
kubectl config current-context
sh development/assessment.sh up
python -B scripts/deploy-assessment-k3d.py
```

On Windows, pass `--openssl "C:/Program Files/Git/usr/bin/openssl.exe"` if
OpenSSL is outside PATH. The installer verifies the current context before
each mutation and uses the `swiftdrop-assessment` namespace. It provisions
private database logins and signing material, waits for PostgreSQL and the
scoped RabbitMQ users, applies the four migration Jobs, grants the relay
publication-only update permission, and rolls out the applications.

Run the local API smoke test through a loopback port-forward in a separate
terminal.

```sh
kubectl --context k3d-swiftdrop -n swiftdrop-assessment port-forward svc/gateway 15173:8080
python -B scripts/test-assessment.py --kubernetes --webhook-site --output assessment-k8s.json
```

Collect sanitized workload and image evidence with the installer's output
and the smoke test's JSON. The smoke test uses only synthetic records and
briefly scales down the local RabbitMQ StatefulSet as an outage test.

## Future cloud prerequisites

Apply the migration and relay secret templates with separate database
principals and validated PostgreSQL TLS. Provision the Identity signing PEM
in `identity-signing` under `signing.pem` and use its key ID in
`identity-secrets`. Provision a managed renewal process for
`notification-workload-tokens` with `delivery.jwt` and `merchant.jwt`.
The current Notification Worker reads both mounted files per request. The
local token sidecar is confined to the assessment overlay.

Verify the target registry image set publishes Linux ARM64 before deploying
to an Oracle ARM64 node. The current backend image publishing workflow
publishes AMD64 only. Use the same immutable release for all application
images and migration Jobs.
