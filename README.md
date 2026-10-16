# SwiftDrop infrastructure

This repository contains the shared Docker development environment and the
optional Kubernetes baseline. The web and backend application services run as
separate containers; Neon PostgreSQL remains an external managed dependency.
Mobile is out of scope for this setup.

The root `compose.yaml` and `compose.application.yaml` are retained as
compatibility entry points for the canonical definition in `development/`.
Use the development runner below for the shared team workflow; do not combine
the two root Compose entry points with each other.

## Run the development stack

Keep the infra, backend, and frontend repositories as siblings:

```text
workspace/
  swiftdrop-infra/
  swiftdrop-backend/
  swiftdrop-frontend/
```

Keep Docker Compose as the shared verification environment. For routine
feature work, a developer may choose to run the apps directly on their host
instead. Host mode requires the backend's pinned .NET 10 SDK, the frontend's
pinned Node/npm versions, and `npm ci` run once in `swiftdrop-frontend`. Both
modes are available through the same script. Start from `swiftdrop-infra`:

```sh
./development/run.sh up --mode docker --build
```

Docker mode builds and starts separate containers for Gateway, Identity,
Delivery, Merchant, Notification Worker, web, and local RabbitMQ. On first
use, the script copies `development/.env.example` to the ignored
`development/.env` and generates a local RabbitMQ password. It never
overwrites an existing configuration. The default scaffold startup does not
require Neon credentials: the sample values are enough to run the containers
and process health checks. Identity's database migration is opt-in through the
`database-migrations` Compose profile. Database-backed Identity routes remain
unavailable until a developer privately provisions and configures
`identity_app` and `identity_migrator` credentials in the ignored `.env`.

Verify the shared baseline without inspecting rendered configuration:

```sh
./development/run.sh ps
curl --fail http://localhost:5000/health
curl --fail http://localhost:5173/
```

Open `http://localhost:5173`; the Gateway is at `http://localhost:5000`.
RabbitMQ's local management UI is `http://localhost:15672` (username
`local_admin`; the generated password is in `development/.env`).

The same script handles common commands and can target one service:

```sh
./development/run.sh up --mode host
./development/run.sh up --mode docker
./development/run.sh up --build delivery-service
./development/run.sh logs gateway
./development/run.sh ps
./development/run.sh down
```

Host mode requires the pinned .NET SDK, Node/npm, frontend dependencies
(`npm ci` in `swiftdrop-frontend`). It starts the backend processes and Vite
frontend without requiring Neon; press Ctrl-C to stop them. If a valid
`IDENTITY_MIGRATION_CONNECTION_STRING` is configured, the runner applies the
Identity migration first. It does not start RabbitMQ; current application
services do not connect to the broker.

**Docker is still a required team acceptance check.** Developers may use host
mode day-to-day, but before merging a change that affects an app or its
container setup, run `./development/run.sh up --mode docker --build` and
confirm the containers and Gateway/web health. This catches Dockerfile,
container-network, Kestrel binding, and Compose regressions that host mode
cannot expose. If Docker is unavailable locally, the change still needs that
check in CI or on a Docker-enabled teammate's machine; do not mark it verified
until the result is recorded.

Supported service names: `rabbitmq`, `identity-service`, `delivery-service`,
`merchant-service`, `notification-worker`, `gateway`, and `web`. `down` keeps
the RabbitMQ data volume.

`scripts/build-images.sh` is retained as a compatibility shortcut for
`./development/run.sh build`; the development runner is the single source for
the build/run behavior.

## Scope and status

The immediate shared-development milestone is scaffold startup: separate
service containers, Gateway/web, RabbitMQ, and process health checks. It does
not depend on Neon or implemented product features. Identity Customer
registration and its migration are present as an initial feature slice, but
registration returns service unavailable until its database connection and
migration are provisioned. Other feature handlers and the Delivery, Merchant,
and Notifications database/broker adapters remain unimplemented. PostgreSQL
is managed externally by Neon and is not run locally.

RabbitMQ is included because the approved service design uses a message
broker, although the current scaffold does not yet connect to it. MinIO is
not part of the everyday development stack; add object storage when Delivery's
proof upload adapter is implemented. This keeps the initial run command to
the services and dependency needed for the current shared baseline.

Kubernetes manifests are separate from this developer workflow. See
`k8s/` for that optional baseline; it has not been runtime-tested.

## Remaining blockers

| Blocker | Owner | Exit condition |
|---|---|---|
| Neon `identity_migrator` role/permissions and Identity migration/readiness have not yet been validated | Identity/infra owners | Only required when beginning DB-backed Identity feature work; provision the restricted migration role, set its private connection string, run the opt-in migration, and confirm `/ready` plus registration persistence |
| Delivery, Merchant, and Notifications migrations/adapters are not implemented | Their service owners | Implement the owned schema, migration, data access, and focused tests before that service's first feature |
| RabbitMQ application publishers/consumers and outbox are not implemented | Delivery/Notifications owners | Exercise a persisted event through broker acknowledgement and consumer deduplication |
| External Neon may not satisfy the assignment's literal database-container requirement | Project lead | Confirm lecturer acceptance or choose an assignment-compliant database deployment |
