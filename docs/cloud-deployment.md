# Cloud Deployment Guide (OCI Always Free / AWS EC2 + K3s + Vercel)

This guide covers provisioning a cloud-independent single-node K3s cluster on Ubuntu Linux (Oracle Cloud Always Free or AWS EC2) for SwiftDrop `staging` and `production` testing environments (HTTP-only ingress, no TLS).

---

## 1. Compute & Firewall Prerequisites

| Provider | Recommended Free-Tier Instance | OS | Required Inbound Firewall / Security Group Ports |
|---|---|---|---|
| **OCI Always Free** | `VM.Standard.A1.Flex` (ARM64, 2–4 OCPU, 12–24 GB RAM) | Ubuntu 22.04/24.04 | `80/tcp` (Public HTTP), `22/tcp` & `6443/tcp` (Trusted IP only) |
| **AWS EC2 Free Tier** | `t2.micro` / `t3.micro` (AMD64, 1 vCPU, 1 GB RAM + 2 GB swap) | Ubuntu 22.04/24.04 | `80/tcp` (Public HTTP), `22/tcp` & `6443/tcp` (Trusted IP only) |

> [!TIP]
> On 1 GB RAM AWS EC2 instances, add a 2 GB swap file before installing K3s (`sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile`).

---

## 2. Bootstrap K3s + NGINX Ingress

On the Ubuntu VM:

```sh
git clone https://github.com/SwiftDrop-Group09/swiftdrop-infra.git
cd swiftdrop-infra
chmod +x scripts/k3s-bootstrap.sh
sudo TRUSTED_CIDR="<YOUR_IP>/32" ./scripts/k3s-bootstrap.sh
```

This installs K3s with `--disable=traefik`, deploys `ingress-nginx` as a `hostNetwork` DaemonSet on port `80` (no cloud load balancer cost), and creates `swiftdrop-staging` and `swiftdrop-prod` namespaces.

---

## 3. Provision Secrets (Neon PostgreSQL, RabbitMQ, GHCR)

1. Create a read-only GitHub Personal Access Token (`read:packages` scope) for pulling images from GHCR.
2. Copy the example env files in `k8s/secrets/*.env.example` to `k8s/secrets/*.env` (ignored by Git) and fill in your Neon PostgreSQL connection strings and RabbitMQ passwords.
3. Run `create-secrets.sh` for the target namespace:

```sh
export GHCR_USERNAME="<github-username>"
export GHCR_READ_TOKEN="<read-packages-pat>"
./k8s/scripts/create-secrets.sh swiftdrop-staging
# or for production:
./k8s/scripts/create-secrets.sh swiftdrop-prod
```

---

## 4. Configure GitHub Actions CD (`infra-cd.yml`)

In `SwiftDrop-Group09/swiftdrop-infra` GitHub repository settings:
1. Create two Environments: `staging` and `production`.
2. In each environment, add secrets:
   - `KUBECONFIG`: Contents of `/etc/rancher/k3s/k3s.yaml` with `127.0.0.1` replaced by the VM's public IP.
   - `GATEWAY_HEALTH_URL`: e.g. `http://api.swiftdrop.example.com/health` (or `http://<VM_PUBLIC_IP>/health` with Host header).
3. **Automatic Staging CD**: In `SwiftDrop-Group09/swiftdrop-backend` secrets, add `INFRA_DISPATCH_TOKEN` (a fine-grained PAT with `Contents: Read` and `Actions: Write` / `Metadata: Read` on `swiftdrop-infra`) so pushing to `main` automatically dispatches `infra-cd.yml` to `staging`.
4. **Manual / Production CD**: Run **Actions → Infra CD → Run workflow**, select `staging` or `production`, and enter the immutable image tag from `backend-images.yml` (e.g., `sha-99dbc98`).

---

## 5. Frontend on Vercel

Connect `SwiftDrop-Group09/swiftdrop-frontend` to Vercel:
- **Framework Preset**: Vite / React Router
- **Environment Variable**: `VITE_API_BASE_URL=http://api.swiftdrop.example.com` (or your staging/prod Gateway URL)
- `vercel.json` handles SPA client-side routing rewrites automatically.

---

## 6. Lightweight Monitoring & Cost Control

- **Workload & Pod Status**: `kubectl get pods -n swiftdrop-prod`
- **Resource Utilization**: `kubectl top pods -n swiftdrop-prod` and `kubectl top nodes`
- **Application Logs**: `kubectl logs -n swiftdrop-prod deployment/gateway --tail=100`
- **Billing Alerts**: Enable budget alerts in OCI Billing / AWS Budgets to guard against accidental paid resource usage.

