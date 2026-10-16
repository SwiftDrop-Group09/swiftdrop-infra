#!/bin/sh
set -eu

rabbit_ctl() {
  rabbitmqctl --erlang-cookie "$RABBITMQ_ERLANG_COOKIE" -n rabbit@rabbitmq "$@"
}

until rabbit_ctl await_startup; do
  sleep 2
done

ensure_user() {
  if rabbit_ctl list_users --silent | cut -f1 | grep -qx "$1"; then
    rabbit_ctl change_password "$1" "$2" >/dev/null
  else
    rabbit_ctl add_user "$1" "$2" >/dev/null
  fi
}

ensure_user delivery_bus "$DELIVERY_BROKER_PASSWORD"
ensure_user worker_bus "$WORKER_BROKER_PASSWORD"

rabbit_ctl set_permissions -p / delivery_bus \
  '^(SwiftDrop\.Contracts\.V1:StatusChanged|delivery-realtime.*)$' \
  '^(SwiftDrop\.Contracts\.V1:StatusChanged|delivery-realtime.*)$' \
  '^(SwiftDrop\.Contracts\.V1:StatusChanged|delivery-realtime.*)$'
rabbit_ctl set_permissions -p / worker_bus \
  '^(SwiftDrop\.Contracts\.V1:StatusChanged|notification-status.*)$' \
  '^(SwiftDrop\.Contracts\.V1:StatusChanged|notification-status.*)$' \
  '^(SwiftDrop\.Contracts\.V1:StatusChanged|notification-status.*)$'
