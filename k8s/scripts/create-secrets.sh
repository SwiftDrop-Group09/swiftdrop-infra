#!/bin/sh
set -eu

namespace=${1:-swiftdrop-dev}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
secret_dir=$(CDPATH= cd -- "$script_dir/../secrets" && pwd)

kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -

create_secret() {
  name=$1
  file=$2
  if [ ! -f "$file" ]; then
    printf 'Missing secret env file: %s\n' "$file" >&2
    exit 1
  fi
  kubectl create secret generic "$name" \
    --namespace "$namespace" \
    --from-env-file="$file" \
    --dry-run=client \
    -o yaml | kubectl apply -f -
}

create_secret identity-secrets "$secret_dir/identity.env"
create_secret delivery-secrets "$secret_dir/delivery.env"
create_secret merchant-secrets "$secret_dir/merchant.env"
create_secret notifications-secrets "$secret_dir/notifications.env"
create_secret rabbitmq-secrets "$secret_dir/rabbitmq.env"

if [ -n "${GHCR_USERNAME:-}" ] && [ -n "${GHCR_READ_TOKEN:-}" ]; then
  kubectl create secret docker-registry ghcr-pull-secret \
    --namespace "$namespace" \
    --docker-server=ghcr.io \
    --docker-username="$GHCR_USERNAME" \
    --docker-password="$GHCR_READ_TOKEN" \
    --dry-run=client \
    -o yaml | kubectl apply -f -
fi
