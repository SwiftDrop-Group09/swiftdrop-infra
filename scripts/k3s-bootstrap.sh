#!/bin/sh
set -eu

# Idempotent K3s + ingress-nginx bootstrap script for Ubuntu (OCI Always Free / AWS EC2).
# Usage: sudo TRUSTED_CIDR="203.0.113.10/32" ./scripts/k3s-bootstrap.sh

if [ "$(id -u)" -ne 0 ]; then
  printf 'Run this script as root (e.g., sudo TRUSTED_CIDR="<YOUR_IP>/32" ./scripts/k3s-bootstrap.sh).\n' >&2
  exit 1
fi

: "${TRUSTED_CIDR:?Set TRUSTED_CIDR (e.g. 203.0.113.10/32) to restrict SSH (22) and K3s API (6443)}"

printf '==> Configuring UFW firewall (HTTP 80, restricted SSH 22 & K3s API 6443)...\n'
if command -v ufw >/dev/null 2>&1; then
  ufw allow 80/tcp comment "HTTP Ingress"
  ufw allow from "$TRUSTED_CIDR" to any port 22 proto tcp comment "Restricted SSH"
  ufw allow from "$TRUSTED_CIDR" to any port 6443 proto tcp comment "Restricted K3s API"
  ufw --force enable
fi

printf '==> Installing K3s (Traefik disabled in favor of ingress-nginx)...\n'
if ! command -v k3s >/dev/null 2>&1; then
  curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--disable=traefik --write-kubeconfig-mode=600" sh -
fi

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

printf '==> Installing Helm (if missing)...\n'
if ! command -v helm >/dev/null 2>&1; then
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

printf '==> Installing ingress-nginx (NodePort / hostNetwork for free-tier VMs without Cloud LB)...\n'
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx \
  --create-namespace \
  --set controller.hostNetwork=true \
  --set controller.service.type=ClusterIP \
  --set controller.kind=DaemonSet \
  --wait

printf '==> Creating SwiftDrop namespaces...\n'
for ns in swiftdrop-staging swiftdrop-prod; do
  kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
done

printf '==> K3s bootstrap complete!\n'
kubectl get nodes
kubectl get pods -n ingress-nginx

