#!/bin/sh
set -eu
umask 077

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file="$script_dir/.env.assessment"
action=${1:-}

case "$action" in
  up|config|down|ps|verify) ;;
  *)
    printf 'Usage: sh development/assessment.sh up|config|down|ps|verify\n' >&2
    exit 2
    ;;
esac

if [ "$#" -ne 1 ]; then
  printf 'Provide one assessment command.\n' >&2
  exit 2
fi

if [ ! -f "$env_file" ]; then
  if [ "$action" != up ] && [ "$action" != config ]; then
    printf 'No local assessment stack has been configured.\n' >&2
    exit 1
  fi
  temp_env=$(mktemp "$script_dir/.env.assessment.XXXXXX")
  trap 'rm -f "$temp_env"' EXIT
  for key in RABBITMQ_DEFAULT_PASS DELIVERY_BROKER_PASSWORD WORKER_BROKER_PASSWORD \
    ASSESSMENT_POSTGRES_PASSWORD ASSESSMENT_IDENTITY_APP_PASSWORD ASSESSMENT_IDENTITY_MIGRATOR_PASSWORD \
    ASSESSMENT_DELIVERY_APP_PASSWORD ASSESSMENT_DELIVERY_MIGRATOR_PASSWORD ASSESSMENT_DELIVERY_RELAY_PASSWORD \
    ASSESSMENT_MERCHANT_APP_PASSWORD ASSESSMENT_MERCHANT_MIGRATOR_PASSWORD ASSESSMENT_MERCHANT_SERVICE_KEY \
    ASSESSMENT_NOTIFICATIONS_APP_PASSWORD ASSESSMENT_NOTIFICATIONS_MIGRATOR_PASSWORD; do
    password=$(openssl rand -hex 24)
    printf '%s=%s\n' "$key" "$password" >> "$temp_env"
  done
  # Install without replacing an environment file from another invocation.
  if ! ln "$temp_env" "$env_file" 2>/dev/null && [ ! -f "$env_file" ]; then
    printf 'Could not create the local assessment environment file.\n' >&2
    exit 1
  fi
fi

compose() {
  docker compose --env-file "$env_file" -f "$script_dir/compose.yaml" \
    -f "$script_dir/compose.assessment.yaml" "$@"
}

case "$action" in
  config) compose config --quiet ;;
  ps) compose ps ;;
  down) compose down ;;
  verify)
    curl --fail --silent --show-error http://localhost:5000/health >/dev/null
    curl --fail --silent --show-error http://localhost:5173/ >/dev/null
    compose exec -T postgres psql --quiet -v ON_ERROR_STOP=1 -U postgres -d swiftdrop_delivery <<'SQL'
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM (VALUES ('identity_app', 'swiftdrop_identity'),
      ('identity_migrator', 'swiftdrop_identity'),
      ('delivery_app', 'swiftdrop_delivery'),
      ('delivery_migrator', 'swiftdrop_delivery'),
      ('delivery_relay', 'swiftdrop_delivery'),
      ('merchant_app', 'swiftdrop_merchant'),
      ('merchant_migrator', 'swiftdrop_merchant'),
      ('notifications_app', 'swiftdrop_notifications'),
      ('notifications_migrator', 'swiftdrop_notifications')
    ) AS allowed(role_name, owned_database)
    CROSS JOIN pg_database AS database
    WHERE database.datallowconn
      AND has_database_privilege(allowed.role_name, database.datname, 'CONNECT')
          IS DISTINCT FROM (database.datname = allowed.owned_database)
  ) THEN
    RAISE EXCEPTION 'Assessment login database access does not match service ownership';
  END IF;
  IF NOT has_table_privilege('delivery_relay', 'public.outbox_messages', 'SELECT')
    OR NOT has_column_privilege('delivery_relay', 'public.outbox_messages', 'published_at', 'UPDATE')
    OR has_column_privilege('delivery_relay', 'public.outbox_messages', 'payload', 'UPDATE')
    OR has_table_privilege('delivery_relay', 'public.outbox_messages', 'INSERT,DELETE')
    OR has_schema_privilege('delivery_app', 'public', 'CREATE')
  THEN
    RAISE EXCEPTION 'Assessment database operation permissions exceed or omit intended privileges';
  END IF;
END
$$;
SQL
    printf 'Assessment gateway, web and PostgreSQL role checks passed.\n'
    ;;
  up)
    compose build
    compose up --detach --wait postgres
    for migration in identity-migrate delivery-migrate merchant-migrate notification-migrate; do
      compose --profile database-migrations run --rm --no-deps "$migration"
    done
    compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d swiftdrop_delivery <<'SQL'
GRANT SELECT, UPDATE (published_at) ON TABLE public.outbox_messages TO delivery_relay;
SQL
    compose up --detach --wait --wait-timeout 120
    ;;
esac
