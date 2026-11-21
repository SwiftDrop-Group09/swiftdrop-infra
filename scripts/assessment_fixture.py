import json
from importlib import import_module
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:5173"
KUBERNETES = False
TLS_CONTEXT = None
LOCAL_OPENER = build_opener(ProxyHandler({}))
COMPOSE = (
    "docker", "compose", "--env-file", "development/.env.assessment",
    "-f", "development/compose.yaml", "-f", "development/compose.assessment.yaml",
)


class ApiFailure(Exception):
    def __init__(self, status, route):
        self.status = status
        super().__init__(f"{route} returned HTTP {status}")


def request(path, payload=None, token=None, method=None, idempotency_key=None, extra_headers=None, raw=None):
    headers = {"Accept": "application/json", **(extra_headers or {})}
    data = raw
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    try:
        with LOCAL_OPENER.open(Request(ORIGIN + path, data, headers, method=method), timeout=25) as response:
            body = response.read()
            return json.loads(body) if body else None
    except HTTPError as error:
        raise ApiFailure(error.code, path) from None
    except URLError as error:
        raise OSError(f"Local API transport error at {path.split('?')[0]} ({type(error.reason).__name__})") from None


def new_credentials(name):
    return {"email": f"performance-{name}-{secrets.token_hex(6)}@example.com",
            "password": secrets.token_hex(18)}


def login(credentials):
    deadline = time.monotonic() + 75
    while True:
        try:
            return request("/api/v1/auth/login", credentials)["accessToken"]
        except ApiFailure as error:
            if error.status != 429 or time.monotonic() >= deadline:
                raise
            time.sleep(3)


def cluster():
    return import_module("deploy-assessment-k3d")


def use_kubernetes(ca_file=None):
    global KUBERNETES, ORIGIN, TLS_CONTEXT, LOCAL_OPENER
    cluster().guard()
    KUBERNETES = True
    ORIGIN = "http://127.0.0.1:15173"
    if ca_file:
        ORIGIN = "https://localhost:18443"
        TLS_CONTEXT = ssl.create_default_context(cafile=str(ca_file))
        LOCAL_OPENER = build_opener(ProxyHandler({}), HTTPSHandler(context=TLS_CONTEXT))


def prepare_fixture(customer_count):
    if KUBERNETES:
        cluster().guard()
        cluster().kube("rollout", "status", "deployment/identity-service", "--timeout=20s")
    else:
        verify_compose_target()

    admin = new_credentials("admin")
    provision_admin(admin)
    return seed_users(admin, customer_count)


def verify_compose_target():
    if not (ROOT / "development/.env.assessment").is_file():
        raise SystemExit("Start the local assessment stack first.")
    published_web = subprocess.check_output(
        (*COMPOSE, "port", "web", "8080"), cwd=ROOT, text=True,
    ).strip()
    if published_web != "127.0.0.1:5173":
        raise SystemExit("Expected the assessment web service on 127.0.0.1:5173.")



def provision_admin(admin):
    provision = {
        "Identity__DevelopmentAdmin__Email": admin["email"],
        "Identity__DevelopmentAdmin__Password": admin["password"],
        "Identity__DevelopmentAdmin__FullName": "Performance Admin",
    }
    if KUBERNETES:
        command = " && ".join(f"read -r {key}" for key in provision)
        command += " && export " + " ".join(provision)
        command += " && exec dotnet SwiftDrop.Identity.Api.dll --provision-development-admin"
        cluster().kube("exec", "-i", "deployment/identity-service", "--", "sh", "-ec", command,
                       data=("\n".join(provision.values()) + "\n").encode(), mutate=True, sensitive=True)
        return
    environment_args = [arg for key in provision for arg in ("-e", key)]
    result = subprocess.run(
        (*COMPOSE, "exec", "-T", *environment_args, "identity-service",
         "dotnet", "SwiftDrop.Identity.Api.dll", "--provision-development-admin"),
        cwd=ROOT, env={**os.environ, **provision}, capture_output=True, check=False,
    )
    if result.returncode:
        raise SystemExit("Could not provision the synthetic assessment admin.")


def seed_users(admin, customer_count):
    admin_token = login(admin)

    def create_user(role):
        credentials = new_credentials(role.lower())
        request("/api/v1/users", {
            **credentials, "fullName": f"Performance {role}", "isActive": True, "role": role,
        }, admin_token)
        return credentials

    customers = [create_user("Customer") for _ in range(customer_count)]
    courier = create_user("Courier")
    suffix = secrets.token_hex(6)
    postal_code = str(secrets.randbelow(70000) + 20000)
    zone_id = request("/api/v1/zones", {
        "name": f"Performance {suffix}", "city": "Colombo", "servicePostalCodes": [postal_code],
    }, admin_token)["id"]
    request("/api/v1/pricing-tiers", {
        "zoneId": zone_id, "size": "Small", "pricingVersion": suffix, "baseRate": 100, "perKgRate": 10,
    }, admin_token)
    courier_token = login(courier)
    courier_id = request("/api/v1/couriers/me/duty", token=courier_token)["courierId"]
    request(f"/api/v1/admin/couriers/{courier_id}", {
        "active": True, "onDuty": True, "capacity": 10, "vehicleType": "Motorcycle",
        "maxWeightKg": 10, "zoneIds": [zone_id],
    }, admin_token, method="PUT")

    address = {"street": "Performance Test Street", "city": "Colombo",
               "postalCode": postal_code, "zoneId": zone_id}
    quote = {
        "pickupAddress": address, "deliveryAddress": address,
        "packageDetails": {"weightKg": 1, "size": "Small"},
        "recipient": {"name": "Synthetic Recipient", "phoneNumber": "0770000000"},
    }
    return [login(customer) for customer in customers], admin_token, courier_id, courier_token, quote
