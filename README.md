# SwiftDrop infrastructure

This repository contains the shared Docker development environment and the
optional Kubernetes baseline. The web and backend application services run as
separate containers; Neon PostgreSQL is the selected managed database provider.
Mobile is out of scope for this setup.

The root `compose.yaml` and `compose.application.yaml` are retained as
compatibility entry points for the canonical definition in `development/`.
Use the development runner below for the shared team workflow; do not combine
the two root Compose entry points with each other.

## Prerequisites

| Tool                                                                                                      | Needed for                                                  |
| --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Docker Desktop (running)                                                                                  | Docker mode (default, and required before merging)          |
| .NET 10 SDK (pinned by `swiftdrop-backend/global.json`)                                                 | Host mode and host migrations                               |
| Node.js / npm (pinned by `swiftdrop-frontend/.nvmrc`) and `npm ci` run once in `swiftdrop-frontend` | Host mode                                                   |
| `curl`                                                                                                  | Host mode health checks and the verification commands below |

Windows users need PowerShell 5.1+ (included with Windows) because `run.bat`
uses it to generate the local RabbitMQ password. `run.sh` needs a POSIX shell
(macOS, Linux, Git Bash or WSL).

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
instead. Both modes are available through the same script. Start from
`swiftdrop-infra`.

**macOS / Linux / Git Bash / WSL**

```sh
./development/run.sh up --mode docker --build
```

**Windows (PowerShell or Command Prompt)**

```powershell
cd path\to\swiftdrop-infra
development\run.bat up --mode docker --build
```

In PowerShell, `.\development\run.bat ...` also works. Every command in this
document has a `run.sh` form and a `run.bat` form with identical arguments;
only the path separator and script name differ.

Docker mode builds and starts separate containers for Gateway, Identity,
Delivery, Merchant, Notification Worker, web, and local RabbitMQ. The first
build downloads several base images and can take 10 to 20 minutes on a slow
connection; later builds use the cache.

On first use, the script copies `development/.env.example` to the ignored
`development/.env` and generates a local RabbitMQ password unless a private
`RABBITMQ_DEFAULT_PASS` override is exported. On later runs it keeps the
existing password, so the value in `.env` stays in step with the RabbitMQ data
volume. It also preserves existing private connection strings. The default
scaffold startup does not require Neon credentials: the sample values are
enough to run the containers and process health checks. Identity, Delivery, and
Notification Worker migrations are explicit one-shot tasks through the
`database-migrations` Compose profile; `up` never applies migrations. Database-backed Identity routes
remain unavailable until its Neon database, runtime role, migration role and
schema are provisioned.

Verify the shared baseline without inspecting rendered configuration:

```sh
./development/run.sh ps
curl --fail http://localhost:5000/health
curl --fail http://localhost:5173/
```

On Windows, use `development\run.bat ps`. In PowerShell, `curl` is an alias
for `Invoke-WebRequest`; use `curl.exe --fail http://localhost:5000/health` or
open the URLs in a browser.

Open `http://localhost:5173`; the Gateway is at `http://localhost:5000`.
RabbitMQ's local management UI is `http://localhost:15672` (username
`local_admin`; the generated password is in `development/.env`). Services can
take up to a minute after `up` returns before their health checks pass.

## Common commands

The runner handles the common commands and can target one service. Replace
`./development/run.sh` with `development\run.bat` on Windows.

| Command                                                            | What it does                                                        |
| ------------------------------------------------------------------ | ------------------------------------------------------------------- |
| `up --mode docker --build`                                       | Build images and start the whole stack (default mode is `docker`) |
| `up`                                                             | Start the stack using existing images (no rebuild)                  |
| `up --build delivery-service`                                    | Rebuild and start one service                                       |
| `up --mode host`                                                 | Run the backend and frontend directly on your machine               |
| `build [service]`                                                | Build images only                                                   |
| `ps`                                                             | Show container status                                               |
| `logs [service]`                                                 | Follow logs (see [Viewing logs](#viewing-logs))                      |
| `config`                                                         | Validate the Compose configuration (`config --quiet`)             |
| `down`                                                           | Stop and remove the containers; the RabbitMQ data volume is kept    |
| `migrate [--mode docker\|host] identity-service\|delivery-service` | Run one explicit database migration                                 |

Supported service names: `rabbitmq`, `identity-service`, `delivery-service`,
`merchant-service`, `notification-worker`, `gateway`, and `web`.

Rules enforced by the runner: `--build` is only valid with `up` and Docker
mode; `down`, `ps` and `config` operate on the whole stack and reject a
service name; host mode supports only full-stack `up` and `migrate`.

## Host mode

```sh
./development/run.sh up --mode host
```

Host mode requires the pinned .NET SDK, Node/npm, and frontend dependencies
(`npm ci` in `swiftdrop-frontend`). By default it starts backend processes with
`dotnet run` and the Vite frontend without requiring Neon for liveness. Add
`--watch` to enable backend hot reload:

```sh
./development/run.sh up --mode host --watch
```

Watch mode automatically uses .NET's polling file watcher so all backend
services can run together on Linux systems with a low inotify limit.

On Windows each process opens in
its own console window; close the window or press Ctrl-C in it to stop that
process. Each backend receives only its owning database setting. Gateway and
web receive no database or broker credentials. Host startup performs no
migration, including when migration credentials are configured. It does not
start RabbitMQ. The Notification Worker consumes only when RabbitMQ, its
Notifications database, and dedicated `worker_bus` credentials are configured.

## Viewing logs

Follow the last 100 lines and stream new output (Ctrl-C stops watching; the
containers keep running):

```sh
./development/run.sh logs                 # all services
./development/run.sh logs gateway         # one service
./development/run.sh logs identity-service
```

For more control, use Docker Compose directly from `swiftdrop-infra`:

```sh
docker compose --env-file development/.env -f development/compose.yaml logs gateway --tail 50
docker compose --env-file development/.env -f development/compose.yaml logs -f -t gateway identity-service
docker compose --env-file development/.env -f development/compose.yaml logs --since 10m
```

(`--tail N` limits lines, `-f` follows, `-t` adds timestamps, `--since` limits
the time window. Use backslashes in the paths on Windows if your shell needs
them.) To save logs to a file for sharing, redirect the output, and remove
connection strings and passwords before posting it anywhere:

```powershell
docker compose --env-file development\.env -f development\compose.yaml logs gateway --tail 200 > gateway.log
```

Where to look first:

| Symptom                     | Look at                                                                                                                                                                   |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| API call returns 500 or 502 | `gateway`, then the service it forwards to                                                                                                                              |
| Registration or login fails | `identity-service`                                                                                                                                                      |
| Booking or delivery errors  | `delivery-service`                                                                                                                                                      |
| Messaging problems          | `rabbitmq`, `notification-worker`                                                                                                                                     |
| Blank page or UI error      | Browser DevTools (F12): Console and Network tabs. The `web` container serves the production build through nginx, so its log shows only HTTP requests, not React errors. |

In host mode, logs are printed in each service's own console window.

## Private Neon configuration and migrations

Supply these exact secret names through an ignored `development/.env` or an
exported environment populated privately by your secret store. Exported values
override the file in both host and Docker mode. Use Npgsql connection strings
with the provisioned Neon endpoint, TLS settings, role and owning database;
the examples contain placeholders only.

| Secret                                       | Recipient / use                                                                                                      |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `RABBITMQ_DEFAULT_PASS`                    | RabbitMQ administrator only, never application workloads                                                             |
| `WORKER_BROKER_PASSWORD`                   | Notification Worker `worker_bus` RabbitMQ password; never use the administrator password                          |
| `DELIVERY_BROKER_PASSWORD`                 | Delivery `delivery_bus` RabbitMQ password                                                                         |
| `IDENTITY_DATABASE_CONNECTION_STRING`      | Identity `ConnectionStrings__Database`, database `swiftdrop_identity`                                            |
| `DELIVERY_DATABASE_CONNECTION_STRING`      | Delivery `ConnectionStrings__Database`, database `swiftdrop_delivery`; also the explicit Delivery migration task |
| `MERCHANT_DATABASE_CONNECTION_STRING`      | Merchant `ConnectionStrings__Database`, database `swiftdrop_merchant`                                            |
| `NOTIFICATIONS_DATABASE_CONNECTION_STRING` | Notification Worker runtime database `swiftdrop_notifications`                                                  |
| `NOTIFICATIONS_MIGRATION_CONNECTION_STRING` | Explicit Notification Worker migration task; DDL-capable role on `swiftdrop_notifications`                       |
| `IDENTITY_MIGRATION_CONNECTION_STRING`     | Explicit Identity task's `ConnectionStrings__MigrationDatabase`, database `swiftdrop_identity`                   |

For Docker mode, Compose maps the Worker credential to
`Notification__Broker__Password`, host `rabbitmq`, username `worker_bus`, and
virtual host `/`. Provision the dedicated RabbitMQ users and permissions before
enabling consumption. If the Notifications database or broker credentials are
missing, the Worker remains live but logs that message consumption is disabled.
Run `development/run.sh migrate notification-worker` (or
`development\run.bat migrate notification-worker` on Windows) after provisioning
the Notifications database and before enabling the consumer.

For internal routing, mount privately provisioned, short-lived worker service
JWTs as `development/secrets/delivery.jwt` and `development/secrets/merchant.jwt`.
Compose mounts this directory read-only into the Worker and reads each file on
every dispatch, allowing token rotation by replacing the file. The Delivery token
must use `aud=delivery-internal` and `scope=notifications:resolve`; the Merchant
token must use `aud=merchant-internal` and `scope=webhooks:dispatch`; both require
the configured Identity issuer and a lifetime no longer than five minutes. The
Worker does not mint tokens, and these files must not be committed.

Review the backend's generated migration SQL before applying it to a shared
database. Provision the roles and permissions privately; the runner creates no
roles or grants. Identity uses its separately provisioned DDL-capable migration
login. Delivery reuses its existing secret for migration, so the role supplied
for that explicit task must own or have the necessary DDL rights on the Delivery
schema and its tables, functions and triggers, plus the EF migration history.
Notification Worker uses its separate migration login; grant it the DDL rights
needed to create the schema objects and EF migration history. Runtime roles
remain separate and restricted. No extra Delivery migration secret or fixed
database role name is required.
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

`scripts/build-images.sh` is retained as a compatibility shortcut for
`./development/run.sh build`; the development runner is the single source for
the build/run behavior.

## Troubleshooting

**`Unknown mode: --mode` when running `run.bat`.** Fixed in the current
`run.bat`. The old script read the argument after `shift` inside a parenthesized
block, where `cmd` had already expanded it. Pull the latest `swiftdrop-infra`.
Docker is the default mode, so `development\run.bat up --build` also works on
an old copy.

**RabbitMQ exits immediately; its log shows `.erlang.cookie: eacces`.** The
`rabbitmq-data` volume holds bad state, usually from an earlier failed start.
It contains no application data, so reset it:

```sh
./development/run.sh down
docker volume rm swiftdrop-development_rabbitmq-data
./development/run.sh up
```

Read its log with `./development/run.sh logs rabbitmq`.

**RabbitMQ rejects the password after `.env` changed.** RabbitMQ stores the
administrator password in its data volume when the volume is first created.
If you change `RABBITMQ_DEFAULT_PASS` later, reset the volume as above so the
new value is applied. The runner no longer regenerates the password on every
run, so this only happens if you edit or delete it yourself.

**`Address already in use` or `ports are not available` on `up`.** Another
process already holds a published port (5000 Gateway, 5173 web, 15672 RabbitMQ
management). Find and stop it:

```powershell
netstat -ano | findstr ":5000 :5173 :15672"
tasklist /FI "PID eq <pid>"
taskkill /PID <pid> /F
```

Typical causes are a leftover host-mode console window (`dotnet run` or
`npm run dev`), another project, or a stale container from a previous run. If
`netstat` shows nothing, Windows may have reserved the port range; check with
`netsh interface ipv4 show excludedportrange protocol=tcp` and, from an
administrator prompt, run `net stop winnat` then `net start winnat`. Afterwards
run `./development/run.sh up` again; images are already built, so `--build` is
not needed. Do not run host mode and Docker mode at the same time, since both
use the same ports.

**`localhost refused to connect` right after `up`.** Check `./development/run.sh ps`.
If a service is missing or not `healthy`, read its log. Gateway and web start
only after their dependencies are healthy, so a failing RabbitMQ or backend
service keeps them from starting.

**Identity registration returns "service unavailable".** Expected until the
Neon database, roles, schema and migration are provisioned; see the Neon
section above.

**`run.bat` shows strange label or syntax errors.** The file must use Windows
(CRLF) line endings. The repository's `.gitattributes` should contain
`*.bat text eol=crlf`; if you edited the file with a tool that wrote LF
endings, convert it back.

## Scope and status

The default integration branch is `dev` in all five repositories. Identity
registration/login/profile connections, Delivery quotes/booking/courier handlers,
and the Merchant onboarding/key persistence slice are present. Real routes still
require owning Neon schemas and authentication integration; Delivery and Merchant
currently use fail-closed IAM seams. Process liveness is not feature readiness.
Notifications persistence and the RabbitMQ relay/consumers remain deferred.

Courier completion also needs a privately provisioned S3-compatible bucket. Set
`DELIVERY_PROOF_STORAGE_ENDPOINT`, `BUCKET`, `REGION`, `ACCESS_KEY`, `SECRET_KEY`
and optional `SESSION_TOKEN` with that common prefix in ignored
`development/.env` or exported variables. Both host and Docker modes map them
only to Delivery's `ProofStorage__*` settings. The adapter requires owner-only
ACLs, no resource-policy Allow and server-side encryption. Missing storage
configuration fails completion without committing Delivered. No shared MinIO
container or automatic bucket provisioning is introduced.

The database/broker inputs are listed above. No real Neon or bucket migration
is performed by runner tests; they use synthetic inputs and disposable fixtures.

Kubernetes manifests are separate from this developer workflow. See
`k8s/` for that optional baseline; it has not been runtime-tested and still
contains future broker/object-store credentials outside the current local setup
workflow. Reconcile it with implemented adapters before deploying it.

Run the runner checks without Docker or Neon:

```sh
python scripts/test-development.py
```

These checks use an isolated temporary fixture and mocked tools to verify
owning-service secret scoping, explicit migration dispatch and configuration
validation. They do not start or migrate the shared stack.

## Remaining blockers

| Blocker                                                                                        | Owner                         | Exit condition                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Provisioned Neon roles, schema privileges and database-backed readiness have not been verified | Service/infra owners          | Privately validate owning-role permissions, run each explicit migration after SQL review, and verify persistence; no live Neon validation is claimed by this repository |
| Merchant human-token integration and Notifications persistence remain incomplete               | Their service owners          | Implement the owned schema, migration, data access, and focused tests before that service's first feature                                                               |
| Delivery event publishers/outbox relay and end-to-end notification effects remain incomplete   | Delivery/Notifications owners | Exercise a committed status event through broker acknowledgement, consumer deduplication, and durable notification/provider effects                                    |
| Container acceptance remains separate from static and host checks                              | App/infra owners              | Run Docker image build/start and record Gateway/web and container health before merge                                                                                   |

## Delivery authentication

Delivery verifies Identity's RS256 access tokens with public keys from JWKS. The
host runner uses Identity's loopback address; Docker uses `identity-service` DNS.
Both configure matching `Identity__Tokens__Issuer`, `Audience` and `JwksUrl`.
`AllowInsecureJwks=true` is restricted to loopback or that internal development
service hostname. Nonlocal deployments must use HTTPS metadata and their own
issuer/audience values. Delivery receives no Identity private signing key or
Identity database credentials. Apply the backend JWT integration PR before this
configuration; restart the host stack or rebuild Delivery after integration.
