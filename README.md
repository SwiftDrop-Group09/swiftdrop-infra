# SwiftDrop local infrastructure

This repository provides the shared development dependency stack and an
optional containerized application/Kubernetes baseline. Keep the repositories
as siblings in one workspace:

```text
workspace/
  swiftdrop-infra/
  swiftdrop-backend/
  swiftdrop-frontend/
```

Mobile is not included in this baseline. The backend is still a scaffold:
health endpoints prove process liveness, but business handlers, DB/broker/
object-store adapters, migrations and dependency-aware readiness are not
implemented. Providing Neon strings does not mean the applications use Neon
yet.

## Prerequisites

- Docker Engine/Desktop with the Compose plugin, running and available to you
- `openssl` (or another secure random generator) for local passwords
- Backend contributors: the .NET SDK pinned by
  `swiftdrop-backend/global.json`
- Frontend contributors: the Node/npm versions pinned by
  `swiftdrop-frontend/.nvmrc` and `package.json`
- Kubernetes baseline only: `kubectl`, a local kind/minikube cluster, and an
  installed Ingress controller with the `nginx` IngressClass

## Shared local dependencies

From `swiftdrop-infra`:

```sh
cp .env.example .env
chmod 600 .env
```

Replace all four Neon connection-string examples with TLS-enabled credentials
when the Neon project, databases, and roles are ready. Generate distinct local
values for broker/object-store passwords; use only sample local credentials
here, never production secrets. These Neon values are reserved configuration
for backend adapters that have not been implemented.

Validate and start RabbitMQ and private object storage:

```sh
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d
docker compose --env-file .env ps
```

The stack creates separate RabbitMQ Delivery/Worker users and a private
object-store bucket with a Delivery-only policy. PostgreSQL is managed by Neon
and is not started or exposed locally. The RabbitMQ management UI is at
`http://127.0.0.1:15672`; the object-store console is at
`http://127.0.0.1:9001`. Their admin credentials are the local `.env` values.
The template's `latest` object-store tags are unpinned placeholders and must be
replaced with verified versions outside local development.

Stop services without deleting developer data:

```sh
docker compose --env-file .env down
```

## Run applications as local processes

This is the current simplest shared development loop. Start each backend host
in a separate terminal from `swiftdrop-backend`:

```sh
dotnet run --project src/Services/Identity/SwiftDrop.Identity.Api --launch-profile http
dotnet run --project src/Services/Delivery/SwiftDrop.Delivery.Api --launch-profile http
dotnet run --project src/Services/Merchant/SwiftDrop.Merchant.Api --launch-profile http
dotnet run --project src/Workers/SwiftDrop.Notification.Worker --launch-profile http
dotnet run --project src/Gateway/SwiftDrop.Gateway --launch-profile http
```

The Gateway listens at `http://127.0.0.1:5000` and routes to the local backend
hosts. In another terminal from `swiftdrop-frontend`:

```sh
npm ci
cp .env.example .env.local
npm run dev
```

Open `http://localhost:5173`. Vite forwards `/api` and `/graphql` to the
Gateway. Existing feature routes currently return `404` because handlers are
not implemented.

## Build and run the containerized application

Use the app overlay after provisioning local dependencies and replacing
required values in `.env`. From `swiftdrop-infra`:

```sh
docker compose --env-file .env \
  -f compose.yaml \
  -f compose.application.yaml \
  -f ../swiftdrop-backend/deployment/compose.backend.yaml \
  --profile application config --quiet

docker compose --env-file .env \
  -f compose.yaml \
  -f compose.application.yaml \
  -f ../swiftdrop-backend/deployment/compose.backend.yaml \
  --profile application up -d --build
```

The overlay builds the five backend hosts and web app from the sibling
repositories; it binds the Gateway and web ports to loopback only. The backend
Compose override binds Kestrel to container interfaces and exposes Delivery's
HTTP/2 endpoint internally. The web container proxies `/api` and `/graphql`
through the Gateway. Each API/Worker gets only its own Neon string. The app
containers are experimental until an SDK-enabled Docker build and runtime
smoke test have passed; health checks remain liveness-only.

## Kubernetes local baseline

The initial Kustomize base deploys one replica each of Gateway, the three APIs,
Worker and web; RabbitMQ and private MinIO each use a PVC. It also configures
Ingress TLS and bootstrap resources for the private broker/object bucket.
Neon remains external and is intentionally not deployed in Kubernetes.

1. Build images from the workspace root (Docker daemon must be running):

   ```sh
   ./swiftdrop-infra/scripts/build-images.sh
   ```

   The tags are `swiftdrop/{identity,delivery,merchant,notification-worker,
   gateway,web}:local`. Load them into the local cluster using the
   cluster-specific workflow (for example, `kind load docker-image`).
2. Copy each `k8s/secrets/*.env.example` to the corresponding `.env` file in
   `k8s/secrets/`, replace every placeholder, and keep those files private.
   `.env` files in that directory are git-ignored. `ConnectionStrings__Database`
   must be the owning service's own Neon connection string; do not reuse one
   service's credentials for another.
3. Create/update Kubernetes Secrets:

   ```sh
   ./k8s/scripts/create-secrets.sh
   ```
4. Create a local TLS secret for the `swiftdrop.local` host using a developer
   certificate, then apply the manifests:

   ```sh
   kubectl create secret tls swiftdrop-local-tls -n swiftdrop \
     --cert=PATH_TO_CERT --key=PATH_TO_KEY --dry-run=client -o yaml \
     | kubectl apply -f -
   kubectl apply -k k8s/base
   ```

   The namespace is created by the secret script. Ensure the local cluster's
   ingress controller handles the `nginx` IngressClass and that
   `swiftdrop.local` resolves to the local ingress address.

This is a development starter, not production-ready Kubernetes: image tags are
local/unpinned, resource values are initial estimates, no migration Job exists
because the backend has no migrations, and network egress policies are not
defined. The assignment's Kubernetes bonus literally describes deploying the
database as part of the cluster; external Neon does not meet that database
workload requirement unless accepted by the lecturer. The baseline also needs
runtime acceptance on a cluster before it can be considered runnable.

## Remaining blockers and owners

| Blocker | Owner | Exit condition |
|---|---|---|
| Docker daemon unavailable in the current development environment | Infra owner (you) | Start Docker and build/run the app Compose overlay successfully |
| Actual Neon project, TLS endpoints, databases and per-service roles are not provisioned | Infra owner (you) | Replace local placeholders privately and verify connections after adapters exist |
| Backend DB/broker/object-store adapters, migrations and dependency-aware readiness are not implemented | Backend lead (assign a developer) | Implement and test each owning service's integration and migration path |
| Kubernetes cluster, Ingress controller/TLS and runtime behavior are not verified | Infra owner (you) | Deploy on kind/minikube, validate PVC persistence, ingress and restart behavior |
| External Neon may not satisfy the assignment's literal database-container/Kubernetes scope | Project lead (you) | Obtain lecturer acceptance or plan an assignment-only PostgreSQL workload |

Assign named developers to the role owners when the team divides the work; do not
put real credentials in the repository while resolving these blockers.

## Shared connection contract

| Caller | Address |
|---|---|
| Browser in local-process mode | `http://localhost:5173` |
| Local Gateway | `http://127.0.0.1:5000` |
| Compose Gateway/web | `http://127.0.0.1:5000` / `http://127.0.0.1:5173` |
| Kubernetes web | `https://swiftdrop.local` via local Ingress |
| PostgreSQL | Neon-managed TLS endpoint; per-service credentials |
| Compose/Kubernetes dependencies | Private service DNS names |

See `../swiftdrop-frontend/README.md` and
`../swiftdrop-backend/README.md` for the owning repositories' build and test
commands.
