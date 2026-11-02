# SwiftDrop infrastructure

This repository contains the shared Docker development environment and the
optional Kubernetes baseline. The web and backend application services run as
separate containers; Neon PostgreSQL is the selected managed database provider.
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
`development/.env` and generates a local RabbitMQ password unless a private
`RABBITMQ_DEFAULT_PASS` override is exported. It preserves existing private
connection strings. The default scaffold startup does not
require Neon credentials: the sample values are enough to run the containers
and process health checks. Identity and Delivery migrations are explicit
one-shot tasks through the `database-migrations` Compose profile; `up` never
applies migrations. Database-backed Identity routes remain unavailable until
its Neon database, runtime role, migration role and schema are provisioned.

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
frontend without requiring Neon for liveness; press Ctrl-C to stop them. Each
backend receives only its owning database setting. Gateway and web receive no
database or broker credentials. Host startup performs no migration, including
when migration credentials are configured. It does not start RabbitMQ; current
application services do not connect to the broker.

## Private Neon configuration and migrations

Supply these exact secret names through an ignored `development/.env` or an
exported environment populated privately by your secret store. Exported values
override the file in both host and Docker mode. Use Npgsql connection strings
with the provisioned Neon endpoint, TLS settings, role and owning database;
the examples contain placeholders only.

| Secret | Recipient / use |
|---|---|
| `RABBITMQ_DEFAULT_PASS` | RabbitMQ administrator only, never application workloads |
| `IDENTITY_DATABASE_CONNECTION_STRING` | Identity `ConnectionStrings__Database`, database `swiftdrop_identity` |
| `DELIVERY_DATABASE_CONNECTION_STRING` | Delivery `ConnectionStrings__Database`, database `swiftdrop_delivery`; also the explicit Delivery migration task |
| `MERCHANT_DATABASE_CONNECTION_STRING` | Merchant `ConnectionStrings__Database`, database `swiftdrop_merchant` |
| `NOTIFICATIONS_DATABASE_CONNECTION_STRING` | Notification Worker `ConnectionStrings__Database`, database `swiftdrop_notifications` |
| `IDENTITY_MIGRATION_CONNECTION_STRING` | Explicit Identity task's `ConnectionStrings__MigrationDatabase`, database `swiftdrop_identity` |

Review the backend's generated migration SQL before applying it to a shared
database. Provision the roles and permissions privately; the runner creates no
roles or grants. Identity uses its separately provisioned DDL-capable migration
login. Delivery reuses its existing secret for migration, so the role supplied
for that explicit task must own or have the necessary DDL rights on the Delivery
schema and its tables, functions and triggers, plus the EF migration history.
No extra Delivery migration secret or fixed database role name is required.
Keep runtime privileges restricted after migration as your deployment policy
requires; schema permissions must be verified before running either task.

Build the owning image first in Docker mode, then run only the selected task:

```sh
./development/run.sh build identity-service
./development/run.sh migrate --mode docker identity-service
./development/run.sh build delivery-service
./development/run.sh migrate --mode docker delivery-service
# Alternatively, use the pinned .NET SDK without starting the app stack:
./development/run.sh migrate --mode host identity-service
./development/run.sh migrate --mode host delivery-service
```

Migrations do not start dependent containers. Neither ordinary container nor
host startup applies DDL. Never print secret values or rendered Compose
configuration; `./development/run.sh config` validates with `config --quiet`.
No Neon instance is created or contacted by the configuration checks below.

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
migration are provisioned. Delivery Phase 1 includes its booking domain,
PostgreSQL persistence, initial migration and transactional outbox storage;
quote/booking handlers and the broker relay remain later work. Merchant and
Notifications database/broker adapters remain unimplemented. PostgreSQL is
managed externally by Neon; the shared stack has no PostgreSQL container.
Backend persistence tests use an isolated disposable PostgreSQL cluster, not
the team's Neon database.

RabbitMQ is included because the documented service design uses a message
broker, although the current scaffold does not yet connect to it. MinIO is
not part of the everyday development stack; add object storage when Delivery's
proof upload adapter is implemented. This keeps the initial run command to
the services and dependency needed for the current shared baseline.

Kubernetes manifests are separate from this developer workflow. See
`k8s/` for that optional baseline; it has not been runtime-tested and still
contains future broker/object-store credentials outside the current six-secret
workflow. Reconcile it with implemented adapters before deploying it.

Run the runner checks without Docker or Neon:

```sh
python scripts/test-development.py
```

These checks use an isolated temporary fixture and mocked tools to verify
owning-service secret scoping, explicit migration dispatch and configuration
validation. They do not start or migrate the shared stack.

## Remaining blockers

| Blocker | Owner | Exit condition |
|---|---|---|
| Provisioned Neon roles, schema privileges and database-backed readiness have not been verified | Service/infra owners | Privately validate owning-role permissions, run each explicit migration after SQL review, and verify persistence; no live Neon validation is claimed by this repository |
| Merchant and Notifications migrations/adapters are not implemented | Their service owners | Implement the owned schema, migration, data access, and focused tests before that service's first feature |
| Delivery quote/booking handlers, RabbitMQ publishers/consumers and outbox relay are not implemented | Delivery/Notifications owners | Exercise a committed booking event through broker acknowledgement and consumer deduplication |
| Container acceptance remains separate from static and host checks | App/infra owners | Run Docker image build/start and record Gateway/web and container health before merge |
