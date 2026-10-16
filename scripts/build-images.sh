#!/bin/sh
set -eu

infra_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
workspace_dir=$(CDPATH= cd -- "$infra_dir/.." && pwd)
backend_dir="$workspace_dir/swiftdrop-backend"
frontend_dir="$workspace_dir/swiftdrop-frontend"

build_backend() {
  image=$1
  project_file=$2
  docker build \
    --file "$backend_dir/Dockerfile" \
    --build-arg "PROJECT_FILE=$project_file" \
    --tag "swiftdrop/$image:local" \
    "$backend_dir"
}

build_backend identity src/Services/Identity/SwiftDrop.Identity.Api/SwiftDrop.Identity.Api.csproj
build_backend delivery src/Services/Delivery/SwiftDrop.Delivery.Api/SwiftDrop.Delivery.Api.csproj
build_backend merchant src/Services/Merchant/SwiftDrop.Merchant.Api/SwiftDrop.Merchant.Api.csproj
build_backend notification-worker src/Workers/SwiftDrop.Notification.Worker/SwiftDrop.Notification.Worker.csproj
build_backend gateway src/Gateway/SwiftDrop.Gateway/SwiftDrop.Gateway.csproj

docker build --file "$frontend_dir/Dockerfile" --tag swiftdrop/web:local "$frontend_dir"
