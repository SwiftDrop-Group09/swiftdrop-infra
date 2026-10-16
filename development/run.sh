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
  development/run.sh up [--mode docker|host] [--build] [service]
  development/run.sh build [service]              # Docker image build
  development/run.sh down|ps|logs|config          # Docker Compose

Services: rabbitmq, identity-service, delivery-service, merchant-service,
          notification-worker, gateway, web

Docker is the default mode. Host mode starts backend and frontend processes
directly; stop with Ctrl-C. Docker build/start is required before PR merge.
EOF
}

initialize_env() {
  if [ ! -f "$env_file" ]; then
    cp "$example_file" "$env_file"
    chmod 600 "$env_file"
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

if [ "$mode" = host ] && { [ "$action" != up ] || [ -n "$service" ]; }; then
  printf '%s\n' 'Host mode supports only "up" for the full local app stack; stop it with Ctrl-C.' >&2
  exit 2
fi

case "$action" in
  up|build|down|ps|logs|config) ;;
  *)
    printf 'Unknown command: %s\n' "$action" >&2
    usage >&2
    exit 2
    ;;
esac

start_host_stack() {
  backend_dir=$(CDPATH= cd -- "$infra_dir/../swiftdrop-backend" && pwd)
  frontend_dir=$(CDPATH= cd -- "$infra_dir/../swiftdrop-frontend" && pwd)
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

  mkdir -p "$log_dir"
  start_process() {
    name=$1
    log_file="$log_dir/$name.log"
    shift
    (
      cd "$backend_dir"
      export ASPNETCORE_ENVIRONMENT=Development
      export DOTNET_ENVIRONMENT=Development
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
  start_process identity-service dotnet run --no-launch-profile --project src/Services/Identity/SwiftDrop.Identity.Api
  start_process delivery-service dotnet run --no-launch-profile --project src/Services/Delivery/SwiftDrop.Delivery.Api
  start_process merchant-service dotnet run --no-launch-profile --project src/Services/Merchant/SwiftDrop.Merchant.Api
  start_process notification-worker dotnet run --no-launch-profile --project src/Workers/SwiftDrop.Notification.Worker
  start_process gateway dotnet run --no-launch-profile --project src/Gateway/SwiftDrop.Gateway
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

if [ "$mode" = host ]; then
  start_host_stack
  exit 0
fi

initialize_env
cd "$infra_dir"

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

case "$action" in
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
