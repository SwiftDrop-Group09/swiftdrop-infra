# SwiftDrop Infrastructure

This repository runs SwiftDrop's web application, gateway, APIs, workers, and RabbitMQ with Docker Compose. It also contains Kubernetes manifests for assessment and deployment environments.

## Choose a development environment

| Environment | Use it for |
| --- | --- |
| Local PostgreSQL assessment | Run the full stack with isolated local databases and generated credentials. |
| Neon-backed development | Use the team's provisioned databases while running the application in Docker or on the host. |

Keep `swiftdrop-infra`, `swiftdrop-backend`, and `swiftdrop-frontend` as sibling checkouts. Run the commands below from `swiftdrop-infra` with Docker and Compose v2 available. The scripts use a POSIX shell and OpenSSL. On Windows, use Git Bash.

Both environments expose the web app at `http://localhost:5173` and the gateway at `http://localhost:5000`. Stop one environment before starting the other because they use the same loopback ports.

## Local PostgreSQL assessment

```sh
sh development/assessment.sh up
sh development/assessment.sh ps
sh development/assessment.sh verify
```

The assessment runner generates credentials in the ignored `development/.env.assessment` file, starts local PostgreSQL, and applies the four service migrations. The database stays on the Compose network. Keep the environment file with its volumes so generated credentials remain in sync.

Run `sh development/assessment.sh down` to stop the stack and retain its volumes. See the [assessment integration guide](docs/assessment-integration.md) for end-to-end workflow checks.

## Neon-backed development

Copy [development/.env.example](development/.env.example) to the ignored `development/.env` and supply the provisioned Neon connection strings and TLS settings for Identity, Delivery, Merchant, and Notifications. Use the designated migration credentials for schema changes. The runner generates local RabbitMQ credentials when the corresponding values are blank or set to `GENERATE_LOCALLY`.

```sh
sh development/run.sh up --mode docker --build
sh development/run.sh ps
curl --fail http://localhost:5000/health
curl --fail http://localhost:5173/
```

The shell runner checks pending Identity, Delivery, and Merchant migrations before starting the application. Run the Notification Worker migration separately when provisioning its Neon schema, using the image built by the startup command.

```sh
docker compose --env-file development/.env -f development/compose.yaml --profile database-migrations run --rm --no-deps notification-migrate
docker compose --env-file development/.env -f development/compose.yaml restart notification-worker
```

Host mode uses the backend SDK and frontend Node.js versions pinned in their repositories. Install the frontend dependencies with `npm ci` before starting it.

```sh
sh development/run.sh up --mode host
```

Host mode supports `--watch` for backend hot reload. To enable Merchant-to-Delivery order processing in host mode, set the private `MERCHANT_DELIVERY_SERVICE_KEY` in `development/.env`. See the [backend integration guide](https://github.com/SwiftDrop-Group09/swiftdrop-backend/blob/dev/docs/courier-workspace-experience.md) for gRPC configuration.

| Command | Purpose |
| --- | --- |
| `sh development/run.sh logs gateway` | Follow gateway logs. |
| `sh development/run.sh config` | Validate the Compose configuration. |
| `sh development/run.sh up --mode docker --build delivery-service` | Rebuild and start Delivery. |
| `sh development/run.sh down` | Stop the Docker stack and retain its volumes. |

See the [backend development secrets guide](https://github.com/SwiftDrop-Group09/swiftdrop-backend/blob/dev/docs/development-secrets.md) for application configuration. Keep populated environment files and rendered Compose output private.

## Kubernetes and deployment

| Area | Reference |
| --- | --- |
| Kubernetes manifests | [Base](k8s/base/), [environment overlays](k8s/overlays/) |
| Local Kubernetes assessment | [k3d deployment and verification](docs/assessment-kubernetes.md) |
| Cloud deployment | [K3s and web hosting guide](docs/cloud-deployment.md) |
| Performance | [Assessment measurements and methodology](docs/assessment-performance.md) |

## SwiftDrop repositories

| Repository | Responsibility |
| --- | --- |
| [Backend](https://github.com/SwiftDrop-Group09/swiftdrop-backend) | Gateway, APIs, background workers, and integration contracts |
| [Web frontend](https://github.com/SwiftDrop-Group09/swiftdrop-frontend) | React web application and role-based workspaces |
| [Mobile app](https://github.com/SwiftDrop-Group09/swiftdrop-mobile) | Flutter application for customers and couriers |
| [Infrastructure](https://github.com/SwiftDrop-Group09/swiftdrop-infra) | Local development, Docker Compose, and Kubernetes deployment |
| [Project documentation](https://github.com/SwiftDrop-Group09/swiftdrop-docs) | Requirements, architecture, integration design, and assessment evidence |
