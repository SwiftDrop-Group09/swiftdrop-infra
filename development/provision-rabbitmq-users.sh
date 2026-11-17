#!/bin/sh
set -eu

api='http://rabbitmq:15672/api'
admin_auth="local_admin:${RABBITMQ_DEFAULT_PASS}"
attempt=0
until curl --fail --silent --show-error --user "$admin_auth" "$api/overview" >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    printf '%s\n' 'RabbitMQ management API did not become available.' >&2
    exit 1
  fi
  sleep 2
done

put_user() {
  name=$1
  password=$2
  curl --fail --silent --show-error --user "$admin_auth" \
    --request PUT --header 'Content-Type: application/json' \
    --data "{\"password\":\"$password\",\"tags\":\"\"}" \
    "$api/users/$name" >/dev/null
}

put_permissions() {
  name=$1
  configure=$2
  write=$3
  read=$4
  curl --fail --silent --show-error --user "$admin_auth" \
    --request PUT --header 'Content-Type: application/json' \
    --data "{\"configure\":\"$configure\",\"write\":\"$write\",\"read\":\"$read\"}" \
    "$api/permissions/%2F/$name" >/dev/null
}

put_user delivery_bus "$DELIVERY_BROKER_PASSWORD"
put_permissions delivery_bus \
  '^SwiftDrop[.]Contracts[.]V1:StatusChanged$' \
  '^SwiftDrop[.]Contracts[.]V1:StatusChanged$' ''

put_user worker_bus "$WORKER_BROKER_PASSWORD"
put_permissions worker_bus \
  '^(SwiftDrop[.]Contracts[.]V1:StatusChanged|notification-status.*)$' \
  '^SwiftDrop[.]Contracts[.]V1:StatusChanged$' \
  '^notification-status.*$'

printf '%s\n' 'Provisioned least-privilege local RabbitMQ application users.'
