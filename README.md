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
overwrites an existing configuration.

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

Host mode requires the pinned .NET SDK, Node/npm, and frontend dependencies
(`npm ci` in `swiftdrop-frontend`). It starts all five backend processes plus
the Vite frontend using their documented loopback addresses; press Ctrl-C to
stop only those processes. It does not start RabbitMQ; the current scaffold
does not connect to the broker.

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

The backend is currently a scaffold: health endpoints prove process liveness,
but feature handlers, database/broker/object-store adapters and migrations
have not been implemented. The Neon connection-string entries in
`development/.env.example` are placeholders for later service integration;
the containers do not currently use them. PostgreSQL is not run locally.

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
| Docker builds and runtime startup have not been verified in this environment | Infra owner | Run `./development/run.sh up --mode docker --build` on a Docker-enabled machine and record startup results |
| Neon databases/roles and actual TLS credentials are not provisioned | Infra owner | Provision privately and validate when service database adapters exist |
| Backend integrations and migrations are not implemented | Backend lead | Implement and test each service's owned DB/broker integration |
| External Neon may not satisfy the assignment's literal database-container requirement | Project lead | Confirm lecturer acceptance or choose an assignment-compliant database deployment |
