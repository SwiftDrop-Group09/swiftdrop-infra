#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
infra_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)
env_file="$script_dir/.env"
example_file="$script_dir/.env.example"
compose_file="$script_dir/compose.yaml"

usage() {
  cat <<'EOF'
Usage:
  development/run.sh up [--mode docker|host] [--watch] [--build] [service]
  development/run.sh migrate [--mode docker|host] identity-service|delivery-service|merchant-service|notification-worker
  development/run.sh build [service]              # Docker image build
  development/run.sh down|ps|logs|config          # Docker Compose

Services: rabbitmq, identity-service, delivery-service, merchant-service,
          notification-worker, gateway, web

Docker is the default mode. Host mode starts backend and frontend processes
directly; stop with Ctrl-C. Use --watch with host mode to enable backend hot
reload; without it, host mode uses normal dotnet run. Docker build/start is
required before PR merge.
Migrations are explicit one-shot tasks; up never applies database migrations.
EOF
}

initialize_env() {
  if [ ! -f "$env_file" ]; then
    cp "$example_file" "$env_file"
    chmod 600 "$env_file"
  fi

  if [ -n "${RABBITMQ_DEFAULT_PASS:-}" ]; then
    chmod 600 "$env_file"
    return
  fi

  if ! grep -Eq '^RABBITMQ_DEFAULT_PASS=.+$' "$env_file" \
    || grep -Eq '^RABBITMQ_DEFAULT_PASS=GENERATE_LOCALLY$' "$env_file"; then
    if ! command -v openssl >/dev/null 2>&1; then
      printf 'openssl is required to generate a local RabbitMQ password\n' >&2
      exit 1
    fi
    password=$(openssl rand -hex 24)
    temp_file="$env_file.tmp.$$"
    awk -v password="$password" '
      /^RABBITMQ_DEFAULT_PASS=/ {
        print "RABBITMQ_DEFAULT_PASS=" password
        found = 1
        next
      }
      { print }
      END {
        if (!found) print "RABBITMQ_DEFAULT_PASS=" password
      }
    ' "$env_file" > "$temp_file"
    mv "$temp_file" "$env_file"
    chmod 600 "$env_file"
    printf 'Created %s with a generated local RabbitMQ password\n' "$env_file"
  fi
  chmod 600 "$env_file"

  if [ ! -f /tmp/swiftdrop/identity/development.pem ]; then
    if ! command -v openssl >/dev/null 2>&1; then
      printf 'openssl is required to generate a local Identity development signing key\n' >&2
      exit 1
    fi
    mkdir -p /tmp/swiftdrop/identity
    chmod 700 /tmp/swiftdrop/identity
    openssl genpkey -algorithm RSA -out /tmp/swiftdrop/identity/development.pem -pkeyopt rsa_keygen_bits:3072 >/dev/null 2>&1
    chmod 600 /tmp/swiftdrop/identity/development.pem
  fi
}

read_optional_env_value() {
  key=$1
  # CI/private shell inputs override the ignored local file, as with Compose.
  # Avoid sourcing/evaluating connection strings as shell code.
  case "$key" in
    IDENTITY_DATABASE_CONNECTION_STRING)
      value=${IDENTITY_DATABASE_CONNECTION_STRING-}
      present=${IDENTITY_DATABASE_CONNECTION_STRING+1}
      ;;
    DELIVERY_DATABASE_CONNECTION_STRING)
      value=${DELIVERY_DATABASE_CONNECTION_STRING-}
      present=${DELIVERY_DATABASE_CONNECTION_STRING+1}
      ;;
    MERCHANT_DATABASE_CONNECTION_STRING)
      value=${MERCHANT_DATABASE_CONNECTION_STRING-}
      present=${MERCHANT_DATABASE_CONNECTION_STRING+1}
      ;;
    NOTIFICATIONS_DATABASE_CONNECTION_STRING)
      value=${NOTIFICATIONS_DATABASE_CONNECTION_STRING-}
      present=${NOTIFICATIONS_DATABASE_CONNECTION_STRING+1}
      ;;
    NOTIFICATIONS_MIGRATION_CONNECTION_STRING)
      value=${NOTIFICATIONS_MIGRATION_CONNECTION_STRING-}
      present=${NOTIFICATIONS_MIGRATION_CONNECTION_STRING+1}
      ;;
    IDENTITY_MIGRATION_CONNECTION_STRING)
      value=${IDENTITY_MIGRATION_CONNECTION_STRING-}
      present=${IDENTITY_MIGRATION_CONNECTION_STRING+1}
      ;;
    MERCHANT_MIGRATION_CONNECTION_STRING)
      value=${MERCHANT_MIGRATION_CONNECTION_STRING-}
      present=${MERCHANT_MIGRATION_CONNECTION_STRING+1}
      ;;
    DELIVERY_PROOF_STORAGE_ENDPOINT)
      value=${DELIVERY_PROOF_STORAGE_ENDPOINT-}
      present=${DELIVERY_PROOF_STORAGE_ENDPOINT+1}
      ;;
    DELIVERY_PROOF_STORAGE_BUCKET)
      value=${DELIVERY_PROOF_STORAGE_BUCKET-}
      present=${DELIVERY_PROOF_STORAGE_BUCKET+1}
      ;;
    DELIVERY_PROOF_STORAGE_REGION)
      value=${DELIVERY_PROOF_STORAGE_REGION-}
      present=${DELIVERY_PROOF_STORAGE_REGION+1}
      ;;
    DELIVERY_PROOF_STORAGE_ACCESS_KEY)
      value=${DELIVERY_PROOF_STORAGE_ACCESS_KEY-}
      present=${DELIVERY_PROOF_STORAGE_ACCESS_KEY+1}
      ;;
    DELIVERY_PROOF_STORAGE_SECRET_KEY)
      value=${DELIVERY_PROOF_STORAGE_SECRET_KEY-}
      present=${DELIVERY_PROOF_STORAGE_SECRET_KEY+1}
      ;;
    DELIVERY_PROOF_STORAGE_SESSION_TOKEN)
      value=${DELIVERY_PROOF_STORAGE_SESSION_TOKEN-}
      present=${DELIVERY_PROOF_STORAGE_SESSION_TOKEN+1}
      ;;
    *) return 1 ;;
  esac
  if [ -z "$present" ]; then
    value=$(awk -v key="$key" '
    { sub(/\r$/, "") }
    index($0, key "=") == 1 {
      print substr($0, length(key) + 2)
      found = 1
      exit
    }
    END { if (!found) exit 1 }
    ' "$env_file") || return 0
  fi
  case "$value" in
    ""|*ep-your-endpoint*|*REPLACE_*) return 0 ;;
  esac
  printf '%s' "$value"
}

clear_child_secrets() {
  # Give each app only its owning settings; never inherit broker secrets.
  unset RABBITMQ_DEFAULT_PASS WORKER_BROKER_PASSWORD DELIVERY_BROKER_PASSWORD \
    IDENTITY_DATABASE_CONNECTION_STRING \
    DELIVERY_DATABASE_CONNECTION_STRING MERCHANT_DATABASE_CONNECTION_STRING \
    NOTIFICATIONS_DATABASE_CONNECTION_STRING NOTIFICATIONS_MIGRATION_CONNECTION_STRING \
    IDENTITY_MIGRATION_CONNECTION_STRING \
    ConnectionStrings__Database ConnectionStrings__MigrationDatabase \
    DELIVERY_PROOF_STORAGE_ENDPOINT \
    DELIVERY_PROOF_STORAGE_BUCKET \
    DELIVERY_PROOF_STORAGE_REGION \
    DELIVERY_PROOF_STORAGE_ACCESS_KEY \
    DELIVERY_PROOF_STORAGE_SECRET_KEY \
    DELIVERY_PROOF_STORAGE_SESSION_TOKEN \
    ProofStorage__Endpoint ProofStorage__Bucket ProofStorage__Region ProofStorage__AccessKey ProofStorage__SecretKey ProofStorage__SessionToken
}

valid_service() {
  case "$1" in
    rabbitmq|identity-service|delivery-service|merchant-service|notification-worker|gateway|web)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [ "$#" -eq 0 ]; then
  usage
  exit 2
fi

if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
  usage
  exit 0
fi

action=$1
shift
build=0
watch=0
service=
mode=docker

while [ "$#" -gt 0 ]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --build)
      build=1
      ;;
    --watch)
      watch=1
      ;;
    --mode)
      shift
      if [ "$#" -eq 0 ]; then
        printf 'Missing value for --mode (docker or host)\n' >&2
        exit 2
      fi
      mode=$1
      ;;
    --service)
      shift
      if [ "$#" -eq 0 ]; then
        printf 'Missing value for --service\n' >&2
        exit 2
      fi
      service=$1
      ;;
    -*)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [ -n "$service" ]; then
        printf 'Only one service can be selected\n' >&2
        exit 2
      fi
      service=$1
      ;;
  esac
  shift
done

case "$mode" in
  docker|host) ;;
  *)
    printf 'Unknown mode: %s (choose docker or host)\n' "$mode" >&2
    exit 2
    ;;
esac

if [ -n "$service" ] && ! valid_service "$service"; then
  printf 'Unknown service: %s\n' "$service" >&2
  usage >&2
  exit 2
fi

if [ "$build" -eq 1 ] && [ "$action" != up ]; then
  printf '%s\n' '--build is only valid with up' >&2
  exit 2
fi

if [ "$build" -eq 1 ] && [ "$mode" != docker ]; then
  printf '%s\n' '--build is for Docker mode; host mode uses installed SDK tooling' >&2
  exit 2
fi

if [ "$watch" -eq 1 ] && [ "$mode" != host ]; then
  printf '%s\n' '--watch is only valid with host mode' >&2
  exit 2
fi

if [ "$mode" = host ] && [ "$action" != migrate ] \
  && { [ "$action" != up ] || [ -n "$service" ]; }; then
  printf '%s\n' 'Host mode supports full-stack "up" and explicit "migrate"; stop apps with Ctrl-C.' >&2
  exit 2
fi

case "$action" in
  up|build|down|ps|logs|config|migrate) ;;
  *)
    printf 'Unknown command: %s\n' "$action" >&2
    usage >&2
    exit 2
    ;;
esac

if [ "$action" = migrate ]; then
  case "$service" in
    identity-service|delivery-service|merchant-service|notification-worker) ;;
    *)
      printf '%s\n' 'migrate requires identity-service, delivery-service, merchant-service, or notification-worker' >&2
      exit 2
      ;;
  esac
fi

run_host_migration() {
  backend_dir=$(CDPATH= cd -- "$infra_dir/../swiftdrop-backend" && pwd)
  case "$service" in
    identity-service)
      migration_connection=$(read_optional_env_value IDENTITY_MIGRATION_CONNECTION_STRING)
      migration_setting=ConnectionStrings__MigrationDatabase
      migration_project=src/Services/Identity/SwiftDrop.Identity.Api
      ;;
    delivery-service)
      migration_connection=$(read_optional_env_value DELIVERY_DATABASE_CONNECTION_STRING)
      migration_setting=ConnectionStrings__Database
      migration_project=src/Services/Delivery/SwiftDrop.Delivery.Api
      ;;
    merchant-service)
      migration_connection=$(read_optional_env_value MERCHANT_MIGRATION_CONNECTION_STRING)
      [ -z "$migration_connection" ] && migration_connection=$(read_optional_env_value MERCHANT_DATABASE_CONNECTION_STRING)
      migration_setting=ConnectionStrings__Database
      migration_project=src/Services/Merchant/SwiftDrop.Merchant.Api
      ;;
    notification-worker)
      migration_connection=$(read_optional_env_value NOTIFICATIONS_MIGRATION_CONNECTION_STRING)
      migration_setting=ConnectionStrings__Database
      migration_project=src/Workers/SwiftDrop.Notification.Worker
      ;;
  esac
  if [ -z "$migration_connection" ]; then
    printf 'Configure the owning migration connection before migrating %s\n' "$service" >&2
    exit 1
  fi
  if ! command -v dotnet >/dev/null 2>&1; then
    printf 'Host migrations require the .NET SDK pinned by backend/global.json\n' >&2
    exit 1
  fi
  printf 'Applying %s database migrations...\n' "$service"
  (
    cd "$backend_dir"
    clear_child_secrets
    export ASPNETCORE_ENVIRONMENT=Development DOTNET_ENVIRONMENT=Development
    export "$migration_setting=$migration_connection"
    exec dotnet run --no-launch-profile --project "$migration_project" -- --migrate
  )
}

start_host_stack() {
  backend_dir=$(CDPATH= cd -- "$infra_dir/../swiftdrop-backend" && pwd)
  frontend_dir=$(CDPATH= cd -- "$infra_dir/../swiftdrop-frontend" && pwd)
  identity_connection_string=$(read_optional_env_value IDENTITY_DATABASE_CONNECTION_STRING)
  delivery_connection_string=$(read_optional_env_value DELIVERY_DATABASE_CONNECTION_STRING)
  merchant_connection_string=$(read_optional_env_value MERCHANT_DATABASE_CONNECTION_STRING)
  notifications_connection_string=$(read_optional_env_value NOTIFICATIONS_DATABASE_CONNECTION_STRING)
  log_dir="$script_dir/.logs"
  pids=

  if ! command -v dotnet >/dev/null 2>&1; then
    printf 'Host mode requires the .NET SDK pinned by backend/global.json\n' >&2
    exit 1
  fi
  if ! command -v npm >/dev/null 2>&1; then
    printf 'Host mode requires Node.js and npm pinned by frontend/.nvmrc\n' >&2
    exit 1
  fi
  if [ ! -x "$frontend_dir/node_modules/.bin/react-router" ]; then
    printf 'Frontend dependencies are missing; run npm ci in swiftdrop-frontend\n' >&2
    exit 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    printf 'Host mode requires curl to check service health endpoints\n' >&2
    exit 1
  fi

  # Build the shared project graph once before launching concurrent hosts.
  # Parallel dotnet run builds can race when writing SwiftDrop.Contracts.dll.
  (
    cd "$backend_dir"
    clear_child_secrets
    dotnet build SwiftDrop.sln --configuration Debug
  )

  mkdir -p "$log_dir"
  start_process() {
    name=$1
    log_file="$log_dir/$name.log"
    shift
    (
      cd "$backend_dir"
      proof_endpoint=$(read_optional_env_value DELIVERY_PROOF_STORAGE_ENDPOINT)
      proof_bucket=$(read_optional_env_value DELIVERY_PROOF_STORAGE_BUCKET)
      proof_region=$(read_optional_env_value DELIVERY_PROOF_STORAGE_REGION)
      proof_access_key=$(read_optional_env_value DELIVERY_PROOF_STORAGE_ACCESS_KEY)
      proof_secret_key=$(read_optional_env_value DELIVERY_PROOF_STORAGE_SECRET_KEY)
      proof_session_token=$(read_optional_env_value DELIVERY_PROOF_STORAGE_SESSION_TOKEN)
      clear_child_secrets
      export ASPNETCORE_ENVIRONMENT=Development
      export DOTNET_ENVIRONMENT=Development
      case "$name" in
        identity-service) export ConnectionStrings__Database="$identity_connection_string" ;;
        delivery-service)
          export ConnectionStrings__Database="$delivery_connection_string"
          export Identity__Tokens__Issuer="https://identity.swiftdrop.invalid"
          export Identity__Tokens__Audience="swiftdrop-api-local"
          export Identity__Tokens__JwksUrl="http://127.0.0.2:8080/.well-known/jwks.json"
          export Identity__Tokens__AllowInsecureJwks=true
          export ProofStorage__Endpoint="$proof_endpoint"
          export ProofStorage__Bucket="$proof_bucket"
          export ProofStorage__Region="$proof_region"
          export ProofStorage__AccessKey="$proof_access_key"
          export ProofStorage__SecretKey="$proof_secret_key"
          export ProofStorage__SessionToken="$proof_session_token"
          ;;
        merchant-service)
          export ConnectionStrings__Database="$merchant_connection_string"
          export Identity__Tokens__Issuer="https://identity.swiftdrop.invalid"
          export Identity__Tokens__Audience="swiftdrop-api-local"
          export Identity__Tokens__JwksUrl="http://127.0.0.2:8080/.well-known/jwks.json"
          ;;
        notification-worker) export ConnectionStrings__Database="$notifications_connection_string" ;;
      esac
      exec "$@"
    ) > "$log_file" 2>&1 &
    pid=$!
    pids="$pids $pid"
    case "$name" in
      identity-service) identity_pid=$pid ;;
      delivery-service) delivery_pid=$pid ;;
      merchant-service) merchant_pid=$pid ;;
      notification-worker) worker_pid=$pid ;;
      gateway) gateway_pid=$pid ;;
    esac
    printf 'Started %s (pid %s, log %s)\n' "$name" "$pid" "$log_file"
  }

  start_frontend() {
    log_file="$log_dir/web.log"
    (
      cd "$frontend_dir"
      clear_child_secrets
      exec ./node_modules/.bin/react-router dev --host 127.0.0.1
    ) > "$log_file" 2>&1 &
    pid=$!
    pids="$pids $pid"
    web_pid=$pid
    printf 'Started web (pid %s, log %s)\n' "$pid" "$log_file"
  }

  cleanup_host_processes() {
    trap - EXIT INT TERM HUP
    for pid in $pids; do
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
      fi
    done
    for pid in $pids; do
      wait "$pid" 2>/dev/null || true
    done
  }

  check_health() {
    name=$1
    url=$2
    pid=$3
    attempts=0
    until curl --fail --silent "$url" >/dev/null; do
      if ! kill -0 "$pid" 2>/dev/null; then
        printf '%s exited before becoming healthy; inspect %s/%s.log\n' "$name" "$log_dir" "$name" >&2
        return 1
      fi
      attempts=$((attempts + 1))
      if [ "$attempts" -ge 60 ]; then
        printf '%s did not become healthy; inspect %s/%s.log\n' "$name" "$log_dir" "$name" >&2
        return 1
      fi
      sleep 1
    done
    printf 'Ready: %s (%s)\n' "$name" "$url"
  }

  trap cleanup_host_processes EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  if [ "$watch" -eq 1 ]; then
    # Polling avoids exhausting Linux inotify instances when watching five
    # backend projects concurrently, while retaining dotnet watch reloads.
    export DOTNET_USE_POLLING_FILE_WATCHER=1
    start_process identity-service dotnet watch --no-restore --configuration Debug --no-launch-profile --project src/Services/Identity/SwiftDrop.Identity.Api
    start_process delivery-service dotnet watch --no-restore --configuration Debug --no-launch-profile --project src/Services/Delivery/SwiftDrop.Delivery.Api
    start_process merchant-service dotnet watch --no-restore --configuration Debug --no-launch-profile --project src/Services/Merchant/SwiftDrop.Merchant.Api
    start_process notification-worker dotnet watch --no-restore --configuration Debug --no-launch-profile --project src/Workers/SwiftDrop.Notification.Worker
    start_process gateway dotnet watch --no-restore --configuration Debug --no-launch-profile --project src/Gateway/SwiftDrop.Gateway
  else
    start_process identity-service dotnet run --no-build --no-restore --configuration Debug --no-launch-profile --project src/Services/Identity/SwiftDrop.Identity.Api
    start_process delivery-service dotnet run --no-build --no-restore --configuration Debug --no-launch-profile --project src/Services/Delivery/SwiftDrop.Delivery.Api
    start_process merchant-service dotnet run --no-build --no-restore --configuration Debug --no-launch-profile --project src/Services/Merchant/SwiftDrop.Merchant.Api
    start_process notification-worker dotnet run --no-build --no-restore --configuration Debug --no-launch-profile --project src/Workers/SwiftDrop.Notification.Worker
    start_process gateway dotnet run --no-build --no-restore --configuration Debug --no-launch-profile --project src/Gateway/SwiftDrop.Gateway
  fi
  start_frontend
  check_health identity-service http://127.0.0.2:8080/health "$identity_pid"
  check_health delivery-service http://127.0.0.3:8080/health "$delivery_pid"
  check_health merchant-service http://127.0.0.4:8080/health "$merchant_pid"
  check_health notification-worker http://127.0.0.5:8080/health "$worker_pid"
  check_health gateway http://127.0.0.1:5000/health "$gateway_pid"
  check_health web http://127.0.0.1:5173 "$web_pid"

  printf 'Web: http://localhost:5173 (frontend log: %s/web.log)\n' "$log_dir"
  printf 'Press Ctrl-C to stop all host processes started by this command.\n'
  while :; do
    for pid in $pids; do
      if ! kill -0 "$pid" 2>/dev/null; then
        printf 'A host process exited; inspect logs in %s\n' "$log_dir" >&2
        return 1
      fi
    done
    sleep 1
  done
}

initialize_env

if [ "$mode" = host ]; then
  if [ "$action" = migrate ]; then
    run_host_migration
  else
    start_host_stack
  fi
  exit 0
fi

cd "$infra_dir"

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

case "$action" in
  migrate)
    case "$service" in
      identity-service) migration_task=identity-migrate ;;
      delivery-service) migration_task=delivery-migrate ;;
      notification-worker) migration_task=notification-migrate ;;
    esac
    compose --profile database-migrations run --rm --no-deps "$migration_task"
    ;;
  up)
    if [ "$build" -eq 1 ]; then
      if [ -n "$service" ]; then
        compose up -d --build "$service"
      else
        compose up -d --build
      fi
    elif [ -n "$service" ]; then
      compose up -d "$service"
    else
      compose up -d
    fi
    ;;
  build)
    if [ -n "$service" ]; then
      compose build "$service"
    else
      compose build
    fi
    ;;
  down)
    if [ -n "$service" ]; then
      printf 'down stops the whole development stack; do not pass a service\n' >&2
      exit 2
    fi
    compose down
    ;;
  ps)
    if [ -n "$service" ]; then
      printf 'ps lists the whole development stack; do not pass a service\n' >&2
      exit 2
    fi
    compose ps
    ;;
  logs)
    if [ -n "$service" ]; then
      compose logs --follow --tail=100 "$service"
    else
      compose logs --follow --tail=100
    fi
    ;;
  config)
    if [ -n "$service" ]; then
      printf 'config validates the whole development stack; do not pass a service\n' >&2
      exit 2
    fi
    compose config --quiet
    ;;
  *)
    printf 'Unknown command: %s\n' "$action" >&2
    usage >&2
    exit 2
    ;;
esac
