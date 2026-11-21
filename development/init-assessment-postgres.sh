#!/bin/sh
set -eu

for service in identity delivery merchant notifications; do
  database="swiftdrop_$service"
  app="${service}_app"
  migrator="${service}_migrator"
  prefix=$(printf '%s' "$service" | tr '[:lower:]' '[:upper:]')
  export ASSESSMENT_RUNTIME_PASSWORD=$(printenv "${prefix}_APP_PASSWORD")
  export ASSESSMENT_MIGRATION_PASSWORD=$(printenv "${prefix}_MIGRATOR_PASSWORD")
  psql -v ON_ERROR_STOP=1 -U postgres -d postgres \
    -v app="$app" -v migrator="$migrator" -v database="$database" <<'SQL'
\getenv runtime_password ASSESSMENT_RUNTIME_PASSWORD
\getenv migration_password ASSESSMENT_MIGRATION_PASSWORD
CREATE ROLE :"app" LOGIN PASSWORD :'runtime_password';
CREATE ROLE :"migrator" LOGIN PASSWORD :'migration_password';
CREATE DATABASE :"database" OWNER :"migrator";
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO :"app";
SQL
  psql -v ON_ERROR_STOP=1 -U postgres -d "$database" -v app="$app" <<'SQL'
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"app";
SQL
  # Identity migrations grant the explicit DML permissions themselves.
  if [ "$service" != identity ]; then
    psql -v ON_ERROR_STOP=1 -U postgres -d "$database" -v app="$app" -v migrator="$migrator" <<'SQL'
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app";
SQL
  fi
done

psql -v ON_ERROR_STOP=1 -U postgres -d swiftdrop_delivery <<'SQL'
\getenv relay_password DELIVERY_RELAY_PASSWORD
CREATE ROLE delivery_relay LOGIN PASSWORD :'relay_password';
GRANT CONNECT ON DATABASE swiftdrop_delivery TO delivery_relay;
GRANT USAGE ON SCHEMA public TO delivery_relay;
SQL

psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
REVOKE CONNECT ON DATABASE template1 FROM PUBLIC;
SQL
