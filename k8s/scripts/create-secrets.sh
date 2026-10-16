#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
secret_dir=$(CDPATH= cd -- "$script_dir/../secrets" && pwd)

kubectl create namespace swiftdrop --dry-run=client -o yaml | kubectl apply -f -

create_secret() {
  name=$1
  file=$2
  if [ ! -f "$file" ]; then
    printf 'Missing secret env file: %s\n' "$file" >&2
    exit 1
  fi
  kubectl create secret generic "$name" \
    --namespace swiftdrop \
    --from-env-file="$file" \
    --dry-run=client \
    -o yaml | kubectl apply -f -
}

create_secret identity-secrets "$secret_dir/identity.env"
create_secret delivery-secrets "$secret_dir/delivery.env"
create_secret merchant-secrets "$secret_dir/merchant.env"
create_secret notifications-secrets "$secret_dir/notifications.env"
create_secret rabbitmq-secrets "$secret_dir/rabbitmq.env"
create_secret object-store-secrets "$secret_dir/object-store.env"
