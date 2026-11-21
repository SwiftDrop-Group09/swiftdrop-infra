"""Install the isolated Kubernetes assessment pipeline on k3d-swiftdrop."""

import argparse
import base64
import json
import ipaddress
from pathlib import Path
import re
import secrets
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = "k3d-swiftdrop"
NAMESPACE = "swiftdrop-assessment"
APPS = ("identity", "delivery", "merchant", "notification-worker", "delivery-outbox-relay", "gateway", "web")
DATABASES = ("identity", "delivery", "merchant", "notifications")
MIGRATIONS = tuple(f"{service}-migrate" for service in DATABASES)


def run(*args, data=None, sensitive=False):
    result = subprocess.run(args, cwd=ROOT, input=data, capture_output=True, check=False)
    if result.returncode:
        operation = " ".join(args[:3])
        details = "" if sensitive else ": " + result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{operation} failed{details}")
    return result.stdout


def guard():
    if run("kubectl", "config", "current-context").decode().strip() != CONTEXT:
        raise RuntimeError(f"Select {CONTEXT} before using the assessment cluster installer")


def kube(*args, data=None, mutate=False, sensitive=False, scoped=True):
    if mutate:
        guard()
    namespace = ("-n", NAMESPACE) if scoped else ()
    return run("kubectl", "--context", CONTEXT, *namespace, *args, data=data, sensitive=sensitive)


def secret(name, values, secret_type="Opaque"):
    if kube("get", "secret", name, "--ignore-not-found", "-o", "name"):
        return
    manifest = {
        "apiVersion": "v1", "kind": "Secret", "metadata": {"name": name},
        "type": secret_type,
        "data": {key: base64.b64encode(value if isinstance(value, bytes) else value.encode()).decode()
                 for key, value in values.items()},
    }
    kube("create", "-f", "-", data=json.dumps(manifest).encode(), mutate=True, sensitive=True)


def read_secret(name):
    obj = json.loads(kube("get", "secret", name, "-o", "json", sensitive=True))
    return {key: base64.b64decode(value).decode() for key, value in obj["data"].items()}


def provision(openssl):
    keys = ["POSTGRES_PASSWORD", "DELIVERY_RELAY_PASSWORD"] + [
        f"{team.upper()}_{role}_PASSWORD" for team in DATABASES for role in ("APP", "MIGRATOR")
    ]
    secret("postgres-bootstrap", {key: secrets.token_hex(24) for key in keys})
    passwords = read_secret("postgres-bootstrap")
    secret("rabbitmq-secrets", {key: secrets.token_hex(24) for key in (
        "RABBITMQ_DEFAULT_PASS", "RABBITMQ_ERLANG_COOKIE", "DELIVERY_BROKER_PASSWORD", "WORKER_BROKER_PASSWORD",
    )})
    secret("assessment-rpc", {"service-key": secrets.token_hex(24)})
    rpc_key = read_secret("assessment-rpc")["service-key"]

    def conn(team, role, password):
        return f"Host=postgres;Database=swiftdrop_{team};Username={role};Password={password}"

    for team in DATABASES:
        runtime = {"ConnectionStrings__Database": conn(team, f"{team}_app", passwords[f"{team.upper()}_APP_PASSWORD"])}
        if team == "delivery":
            runtime["MerchantIntegration__ServiceKey"] = rpc_key
        if team == "merchant":
            runtime["Merchant__Orders__Delivery__ServiceKey"] = rpc_key
        secret(f"{team}-secrets", runtime)
        key = "ConnectionStrings__MigrationDatabase" if team == "identity" else "ConnectionStrings__Database"
        secret(f"{team}-migration-secrets", {
            key: conn(team, f"{team}_migrator", passwords[f"{team.upper()}_MIGRATOR_PASSWORD"]),
        })
    secret("delivery-relay-secrets", {
        "ConnectionStrings__DeliveryDatabase": conn("delivery", "delivery_relay", passwords["DELIVERY_RELAY_PASSWORD"]),
    })
    secret("merchant-webhook-key", {"encryption-key": secrets.token_hex(32)})
    if not kube("get", "secret", "identity-signing", "--ignore-not-found", "-o", "name"):
        pem = run(openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", sensitive=True)
        secret("identity-signing", {"signing.pem": pem})


def provision_tls(openssl, ca_output):
    if not kube("get", "secret", "assessment-tls", "--ignore-not-found", "-o", "name"):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            root_key, ca, key, csr, cert, extensions = (
                str(directory / name) for name in ("ca.key", "ca.crt", "tls.key", "tls.csr", "tls.crt", "extensions")
            )
            run(openssl, "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "90",
                "-subj", "/CN=SwiftDrop local assessment CA",
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", root_key, "-out", ca, sensitive=True)
            run(openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes",
                "-subj", "/CN=localhost", "-keyout", key, "-out", csr, sensitive=True)
            Path(extensions).write_text(
                "subjectAltName=DNS:localhost\n"
                "basicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature,keyEncipherment\n"
                "extendedKeyUsage=serverAuth\n", encoding="ascii",
            )
            run(openssl, "x509", "-req", "-in", csr, "-CA", ca, "-CAkey", root_key,
                "-CAcreateserial", "-days", "90", "-extfile", extensions, "-out", cert, sensitive=True)
            secret("assessment-tls", {
                "tls.crt": Path(cert).read_bytes(), "tls.key": Path(key).read_bytes(), "ca.crt": Path(ca).read_bytes(),
            }, secret_type="kubernetes.io/tls")
    ca_output.write_text(read_secret("assessment-tls")["ca.crt"], encoding="ascii")
    print(f"Local HTTPS trust certificate: {ca_output}", flush=True)


def trusted_proxy(selector, namespace):
    pods = json.loads(kube("get", "pods", "-n", namespace, "-l", selector, "-o", "json", scoped=False))["items"]
    addresses = [item["status"].get("podIP") for item in pods if item["status"].get("phase") == "Running"
                 and not item["metadata"].get("deletionTimestamp")]
    if len(addresses) != 1 or not addresses[0]:
        raise RuntimeError(f"Expected exactly one running local peer for {selector}")
    return str(ipaddress.ip_address(addresses[0]))


def import_images():
    images = {f"swiftdrop/{name}:local": f"swiftdrop/{name}:dev" for name in APPS}
    images.update({"postgres:17": "postgres:17", "rabbitmq:4-management": "rabbitmq:4-management"})
    refs = {}
    for source, local in images.items():
        image_id = run("docker", "image", "inspect", "--format", "{{.Id}}", local).decode().strip()
        repository = local.rsplit(":", 1)[0]
        immutable = f"{repository}:sha-{image_id.removeprefix('sha256:')[:24]}"
        run("docker", "tag", local, immutable)
        refs[source] = immutable
    nodes = json.loads(kube("get", "nodes", "-o", "json", scoped=False))["items"]
    names = [node["metadata"]["name"] for node in nodes]
    if not names or not all(re.fullmatch(r"k3d-swiftdrop-(server|agent)-\d+", name) for name in names):
        raise RuntimeError("Found an unexpected Kubernetes node name")
    ctr = ("ctr", "--address", "/run/k3s/containerd/containerd.sock", "--namespace", "k8s.io")
    named_refs = {f"docker.io/{value}" if "/" in value else f"docker.io/library/{value}" for value in refs.values()}
    missing = [
        node for node in names
        if not named_refs.issubset(run("docker", "exec", node, *ctr, "images", "ls", "-q").decode().splitlines())
    ]
    if missing:
        print("Loading immutable Linux AMD64 images into k3d nodes.", flush=True)
        with tempfile.TemporaryDirectory() as directory:
            archive = str(Path(directory) / "images.tar")
            run("docker", "save", "--platform", "linux/amd64", "-o", archive, *refs.values())
            remote = "/tmp/swiftdrop-assessment-" + secrets.token_hex(8) + ".tar"
            for node in missing:
                guard()
                run("docker", "cp", archive, f"{node}:{remote}")
                try:
                    guard()
                    run("docker", "exec", node, *ctr, "images", "import", "--platform", "linux/amd64", remote)
                finally:
                    guard()
                    run("docker", "exec", node, "rm", "-f", remote)
    return refs


def apply(resources):
    if resources:
        kube("apply", "-f", "-", data="\n---\n".join(resources).encode(), mutate=True)


def render(refs):
    rendered = kube("kustomize", "k8s/overlays/assessment", scoped=False).decode()
    for source, immutable in refs.items():
        rendered = rendered.replace(f"image: {source}\n", f"image: {immutable}\n")
    groups = {}
    for resource in re.split(r"(?m)^---\s*$", rendered):
        if kind := re.search(r"(?m)^kind: (\S+)", resource):
            groups.setdefault(kind.group(1), []).append(resource)
    return groups


def deploy(openssl, ca_output):
    guard()
    images = import_images()
    groups = render(images)
    apply(groups.pop("Namespace"))
    provision(openssl)
    provision_tls(openssl, ca_output)
    config = kube("create", "configmap", "postgres-bootstrap",
                  "--from-file=10-assessment.sh=development/init-assessment-postgres.sh",
                  "--dry-run=client", "-o", "json")
    kube("apply", "-f", "-", data=config, mutate=True)
    for kind in ("ConfigMap", "Service", "NetworkPolicy", "StatefulSet"):
        apply(groups.pop(kind, []))
    print("Waiting for PostgreSQL and RabbitMQ readiness.", flush=True)
    for stateful in ("postgres", "rabbitmq"):
        kube("rollout", "status", f"statefulset/{stateful}", "--timeout=180s")
    print("Applying the four schema migrations.", flush=True)
    kube("delete", "job", *MIGRATIONS, "--ignore-not-found", "--wait=true", mutate=True)
    apply(groups.pop("Job"))
    kube("wait", "--for=condition=complete", *(f"job/{job}" for job in MIGRATIONS), "--timeout=185s")
    query = b"GRANT SELECT, UPDATE (published_at) ON public.outbox_messages TO delivery_relay;\n"
    kube("exec", "-i", "postgres-0", "--", "psql", "-U", "postgres", "-d", "swiftdrop_delivery",
         "-v", "ON_ERROR_STOP=1", data=query, mutate=True)
    print("Rolling out assessment application workloads.", flush=True)
    for resources in groups.values():
        apply(resources)
    kube("rollout", "status", "deployment", "--timeout=180s")
    ingress_proxy = trusted_proxy("app.kubernetes.io/name=traefik", "kube-system")
    kube("set", "env", "deployment/gateway", f"Gateway__Forwarding__KnownProxies__0={ingress_proxy}", mutate=True)
    kube("rollout", "status", "deployment/gateway", "--timeout=100s")
    gateway_proxy = trusted_proxy("app=gateway", NAMESPACE)
    kube("set", "env", "deployment/identity-service",
         f"Identity__Proxy__KnownProxies__0={gateway_proxy}", mutate=True)
    kube("rollout", "status", "deployment/identity-service", "--timeout=100s")
    print(kube("get", "deploy,statefulset,job,service,ingress,pvc,networkpolicy").decode().strip())
    print(json.dumps(images, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openssl", default="openssl")
    parser.add_argument("--ca-output", type=Path, default=Path(tempfile.gettempdir()) / "swiftdrop-assessment-ca.pem")
    args = parser.parse_args()
    deploy(args.openssl, args.ca_output)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as error:
        raise SystemExit(str(error)) from None