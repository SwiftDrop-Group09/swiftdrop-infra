#!/bin/sh
set -eu

mc alias set local http://object-store:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc mb --ignore-existing local/swiftdrop-private
mc anonymous set none local/swiftdrop-private
mc admin user add local "$DELIVERY_OBJECT_ACCESS_KEY" "$DELIVERY_OBJECT_SECRET_KEY" >/dev/null
mc admin policy create local delivery-private /config/delivery-policy.json
mc admin policy attach local delivery-private --user "$DELIVERY_OBJECT_ACCESS_KEY"
