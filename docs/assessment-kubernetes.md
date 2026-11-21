# Local Kubernetes assessment

Use Docker Desktop with Linux containers, Python 3.11 or later, and the
`k3d-swiftdrop` context. The installer imports the Compose-built Linux AMD64
images into the local k3d nodes using content-derived tags.

```sh
kubectl config current-context
sh development/assessment.sh up
python -B scripts/deploy-assessment-k3d.py
```

On Windows, if OpenSSL is outside PATH, use:

```powershell
python -B scripts/deploy-assessment-k3d.py --openssl "C:/Program Files/Git/usr/bin/openssl.exe" --ca-output "$env:TEMP/swiftdrop-assessment-ca.pem"
```

Start the loopback ingress forward in a separate terminal. Open
`https://localhost:18443/` after trusting the generated local CA in your
browser or test profile. Preserve certificate validation.

```sh
kubectl --context k3d-swiftdrop -n kube-system port-forward svc/traefik 18443:443 18080:80
```

The installer verifies the current context before each mutation and uses the
`swiftdrop-assessment` namespace. It waits for PostgreSQL and scoped RabbitMQ
users, completes four migration Jobs, grants the relay publication-only updates,
and rolls out the applications. It then configures the exact current Traefik
and Gateway proxy addresses. Rerun the installer to refresh those trust
entries after replacing either proxy. Local pod access policies allow ingress
only from Traefik. PostgreSQL, RabbitMQ management and internal APIs stay
cluster-private.

## Verification

```powershell
python -B scripts/test-assessment.py --kubernetes --ca-file "$env:TEMP/swiftdrop-assessment-ca.pem" --webhook-site --output "$env:TEMP/swiftdrop-k8s-e2e.json"
python -B scripts/kubernetes_diagnostics.py --context k3d-swiftdrop --namespace swiftdrop-assessment --output "$env:TEMP/swiftdrop-k8s-status.json"
```

Add `--browser` to complete proof in Chromium and verify the open public tracking
page updates on the same SSE connection. Install Playwright separately from this
repository and set `SWIFTDROP_PLAYWRIGHT_MODULE` to its module directory when it
is outside Node's normal resolution path. Set `SWIFTDROP_BROWSER_EXECUTABLE`
only if using a locally installed Chromium. Run the browser with the local
assessment CA trusted in its certificate store.

GitHub's **Infra CI** manual run also offers `assessment_smoke`. That job
creates an isolated runner-local k3d cluster, tests HTTPS and broker recovery,
and uploads sanitized artifacts. It leaves the external webhook check opt-in
to local runs using the approved receiver.

## Future Oracle Cloud deployment

1. Build and publish the complete backend image set with both platforms.
   Use the same immutable tag for all six releases and their migration Jobs.
   The current backend publishing workflow publishes AMD64 only. Dockerfile
   checks against ARM64 passed, but ARM64 runtime and release publication
   remain pending. For each backend project, publish with:

   ```sh
   docker buildx build --platform linux/amd64,linux/arm64 \
     --build-arg "PROJECT_FILE=$PROJECT_FILE" \
     --tag "$REGISTRY/swiftdrop-$IMAGE_NAME:$RELEASE_TAG" --push ../swiftdrop-backend
   docker buildx imagetools inspect "$REGISTRY/swiftdrop-$IMAGE_NAME:$RELEASE_TAG"
   ```

   Use the project and image mappings in the backend image workflow. If
   self-hosting the web container, build its Dockerfile for both platforms
   and verify the manifest in the same way.
2. Set up the approved Oracle ARM64 K3s environment, DNS and firewall.
   Preserve the existing staging/prod ingress class when using these
   overlays. If choosing packaged Traefik on Oracle instead, use a separate
   Traefik overlay and configure its HTTPS routing before deployment.
   Expose only HTTPS and the HTTP redirect on the public node.
3. Set valid gateway hosts in the cloud overlays, provision the trusted ingress
   certificate as `swiftdrop-gateway-tls`, and set the same HTTPS issuer across
   services. Set Vercel's `BACKEND_API_TARGET` to the HTTPS gateway and configure
   the real frontend origin and OAuth client IDs on their owning workloads.
   Configure the actual ingress and next-hop proxy IP allowlists.
4. Provision four PostgreSQL databases with verified database TLS, separate
   migrators and runtime roles, and the restricted Delivery relay role. Use
   the templates under `k8s/secrets`. Mount the persistent Identity signing
   PEM in the `identity-signing` secret and set its key ID in `identity-secrets`.
   Provide a managed renewal process for the two scoped service JWT files
   in `notification-workload-tokens`. The local development signer stays
   confined to the assessment overlay.
5. Deploy staging first with the immutable image tag. Verify a fresh staging
   installation and an update, public TLS and the redirect, real browser
   login and refresh/logout, and an incremental SSE connection through Vercel.
   These are the outstanding cloud acceptance checks.

[Traefik ingress configuration](https://doc.traefik.io/traefik/routing/providers/kubernetes-ingress/) Â·
[Traefik TLS configuration](https://doc.traefik.io/traefik/reference/routing-configuration/kubernetes/crd/tls/tlsoption/)