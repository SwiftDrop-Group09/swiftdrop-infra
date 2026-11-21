"""Run the synthetic assessment workflow through the real Compose services."""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID
import zlib

import assessment_fixture as fixture
from assessment_fixture import (
    ApiFailure, COMPOSE, ROOT, login, new_credentials, prepare_fixture, request,
)

RECEIVER = "https://webhook.site"
EVENT_TYPE = "delivery.delivered"


class ReceiverFailure(Exception):
    pass


def verify(condition, message):
    if not condition:
        raise RuntimeError(message)


def await_result(check, label, timeout=75):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {label}")


def compose(*args, query=None):
    result = subprocess.run(
        (*COMPOSE, *args), input=query, text=True, capture_output=True, cwd=ROOT, timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Assessment Compose {args[0]} failed")
    return result.stdout.strip()


def db(database, query):
    psql = ("psql", "-At", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", f"swiftdrop_{database}")
    if fixture.KUBERNETES:
        return fixture.cluster().kube("exec", "-i", "postgres-0", "--", *psql,
                                      data=(query + ";\n").encode()).decode().strip()
    return compose("exec", "-T", "postgres", *psql, query=query + ";\n")


def broker_running(running):
    if fixture.KUBERNETES:
        kube = fixture.cluster().kube
        kube("scale", "statefulset/rabbitmq", f"--replicas={int(running)}", mutate=True)
        if running:
            kube("rollout", "status", "statefulset/rabbitmq", "--timeout=120s")
        else:
            kube("wait", "--for=delete", "pod/rabbitmq-0", "--timeout=120s")
    elif running:
        compose("up", "-d", "--wait", "rabbitmq")
    else:
        compose("stop", "rabbitmq")


def external(path, method="GET", body=None):
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if key := os.environ.get("SWIFTDROP_WEBHOOK_SITE_API_KEY"):
        headers["Api-Key"] = key
    try:
        with urlopen(Request(RECEIVER + path, body, headers, method=method), timeout=12) as response:
            return response.read(1_000_000)
    except (URLError, OSError) as error:
        status = getattr(error, "code", None)
        raise ReceiverFailure(f"Webhook.site {method} failed" + (f" (HTTP {status})" if status else "")) from None


@contextmanager
def receiver(enabled):
    if not enabled:
        yield None
        return
    token = str(UUID(json.loads(external("/token", "POST", b'{"default_status":200,"expiry":600}'))["uuid"]))
    try:
        yield token
    finally:
        external(f"/token/{token}", "DELETE")


def prove_webhook(token, secret, event, parcel):
    def find_capture():
        items = json.loads(external(f"/token/{token}/requests?sorting=newest"))["data"]
        for item in items:
            headers = {name.lower(): value for name, value in item["headers"].items()}
            if headers.get("x-swiftdrop-event-id") == [event]:
                return str(UUID(item["uuid"])), headers
        return None

    capture, headers = await_result(find_capture, "automatic webhook")
    raw = external(f"/token/{token}/request/{capture}/raw")
    body = json.loads(raw)
    verify(
        (body["eventId"], body["parcelId"], body["trackingNumber"], body["eventType"])
        == (event, parcel["id"], parcel["trackingNumber"], EVENT_TYPE),
        "Webhook payload did not match the delivered parcel and outbox event",
    )
    timestamp, = headers["x-swiftdrop-timestamp"]
    signature, = headers["x-swiftdrop-signature"]
    verify(abs(time.time() - int(timestamp)) <= 300, "Webhook timestamp is outside its allowed window")
    expected = "sha256=" + hmac.new(
        bytes.fromhex(secret), timestamp.encode("ascii") + b"." + raw, hashlib.sha256,
    ).hexdigest()
    verify(hmac.compare_digest(signature, expected), "Raw webhook HMAC mismatch")


def credentials(role, admin):
    user = new_credentials(role.lower())
    request("/api/v1/users", {**user, "fullName": f"Assessment {role}", "isActive": True, "role": role}, admin)
    return login(user)


def rejected(statuses, path, *args, **kwargs):
    try:
        request(path, *args, **kwargs)
    except ApiFailure as error:
        if error.status in statuses:
            return
        raise
    raise RuntimeError("An invalid or unauthorized request unexpectedly succeeded")


def proof_body(version):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    image = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 6, 8, 2, 0, 0, 0))
    image += chunk(b"IDAT", zlib.compress((b"\0" + b"\x64\x28\xc8" * 8) * 6)) + chunk(b"IEND", b"")
    boundary = secrets.token_hex(16)
    form = b""
    for name, value in {"recipientName": "Synthetic Recipient", "expectedVersion": str(version)}.items():
        form += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    form += f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="proof.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
    return {"Content-Type": f"multipart/form-data; boundary={boundary}"}, form + image + f"\r\n--{boundary}--\r\n".encode()


def advance_to_delivery(parcel_id, version, courier_id, dispatcher, courier):
    assigned = request("/api/v1/deliveries/assign", {
        "parcelId": parcel_id, "courierId": courier_id, "expectedVersion": version,
    }, dispatcher, idempotency_key=secrets.token_hex(12))
    version = assigned["version"]
    for status in ("PickedUp", "InTransit", "OutForDelivery"):
        result = request(f"/api/v1/deliveries/{parcel_id}/status", {"status": status, "expectedVersion": version},
                         courier, method="PUT", idempotency_key=secrets.token_hex(12))
        verify(result["status"] == status, "Unexpected courier status")
        version = result["version"]

    return version


def complete_with_proof(parcel_id, version, courier):
    content_type, proof = proof_body(version)
    return request(f"/api/v1/deliveries/{parcel_id}/pod", token=courier, raw=proof,
                   extra_headers=content_type, idempotency_key=secrets.token_hex(12))


def scenario(token, evidence):
    (customer,), admin, courier_id, courier, quote_input = prepare_fixture(1)
    dispatcher = credentials("Dispatcher", admin)
    applicant = new_credentials("merchant")
    request("/api/v1/auth/register", {**applicant, "fullName": "Synthetic Applicant"})
    merchant = request("/api/v1/merchants/register", {
        "businessName": "Synthetic Merchant " + secrets.token_hex(4),
        "contactEmail": applicant["email"], "contactPhone": "0770000000",
        "pickupAddress": "Assessment Street",
    }, login(applicant))
    request(f"/api/v1/merchants/{merchant['id']}/status", {"status": "Approved"}, admin, method="PUT")
    request(f"/api/v1/users/{merchant['userId']}/roles", {"roles": ["Merchant"]}, admin, method="PUT")
    owner = login(applicant)
    key = request("/api/v1/merchants/api-keys", {"scopes": ["orders:read", "orders:write"]}, owner)["apiKey"]
    if token:
        subscription = request("/api/v1/merchant/webhooks", {"url": f"{RECEIVER}/{token}", "events": [EVENT_TYPE]}, owner)

    quote = request("/api/v1/quotes", quote_input, customer)
    direct = request("/api/v1/parcels", {"quoteId": quote["quoteId"], "acceptTotal": quote["total"]},
                     customer, idempotency_key=secrets.token_hex(12))
    rejected({409}, f"/api/v1/parcels/{direct['id']}/receipt", token=customer)
    rejected({401}, f"/api/v1/parcels/{direct['id']}/receipt")
    rejected({403, 404}, f"/api/v1/parcels/{direct['id']}", token=owner)
    rejected({404}, "/api/v1/parcels/track/TRK-000000-00")
    direct_version = advance_to_delivery(direct["id"], direct["version"], courier_id, dispatcher, courier)
    complete_with_proof(direct["id"], direct_version, courier)
    verify(request(f"/api/v1/parcels/{direct['id']}/receipt", token=customer), "Missing owner receipt")
    evidence["directBookingAndAccessChecks"] = True

    orders = "/api/v1/merchant/orders"
    order = {**quote_input, "externalOrderId": secrets.token_hex(12), "acceptedPriceVersion": quote["pricingVersion"]}
    idem = order["externalOrderId"]
    headers = {"X-API-Key": key}
    rejected({401}, orders, order, idempotency_key=idem)
    accepted = request(orders, order, idempotency_key=idem, extra_headers=headers)
    replayed = request(orders, order, idempotency_key=idem, extra_headers=headers)
    verify(accepted["id"] == replayed["id"], "Merchant order replay created a new job")
    rejected({409}, orders, {**order, "packageDetails": {"weightKg": 2, "size": "Small"}},
             idempotency_key=idem, extra_headers=headers)

    def booked():
        job = request(f"{orders}/{accepted['id']}", extra_headers=headers)
        return job if job.get("parcelId") else None

    job = await_result(booked, "asynchronous merchant booking")
    parcel_id = str(UUID(job["parcelId"]))
    parcel = request(f"/api/v1/parcels/{parcel_id}", token=admin)
    evidence["scopedIdempotentMerchantBooking"] = True
    version = advance_to_delivery(parcel_id, parcel["version"], courier_id, dispatcher, courier)
    try:
        broker_running(False)
        delivered = complete_with_proof(parcel_id, version, courier)
        verify(delivered["status"] == "Delivered", "Proof did not complete the parcel")
        event = db("delivery", f"SELECT id FROM outbox_messages WHERE payload->>'parcelId' = '{parcel_id}' AND payload->>'status' = 'Delivered' AND published_at IS NULL")
        event = str(UUID(event))
        evidence["proofCommittedDuringOutage"] = True
    finally:
        broker_running(True)

    await_result(lambda: db("delivery", f"SELECT id FROM outbox_messages WHERE id = '{event}' AND published_at IS NOT NULL"),
                 "outbox publication after broker recovery")
    direct_id = str(UUID(direct["id"]))
    direct_event = str(UUID(await_result(
        lambda: db("delivery", f"SELECT id FROM outbox_messages WHERE payload->>'parcelId' = '{direct_id}' AND payload->>'status' = 'Delivered' AND published_at IS NOT NULL"),
        "direct customer outbox publication",
    )))
    await_result(lambda: db("notifications", f"""SELECT count(*) FROM consumed_status_events WHERE "EventId" = '{event}'""") == "1",
                 "exactly one broker consumption of the recovered merchant event")
    await_result(lambda: db("notifications", f"""SELECT count(*) FROM notification_jobs WHERE "EventId" = '{direct_event}'
        AND (("Channel" = 'MockSms' AND "State" = 'Sent') OR ("Channel" = 'InApp' AND "State" = 'Persisted'))""") == "2",
        "one sent mock SMS and one persisted in-app notification")
    verify(request(f"/api/v1/parcels/track/{delivered['trackingNumber']}")["status"] == "Delivered",
           "Public tracking did not reflect proof completion")
    evidence["brokerRecoveryAndNotifications"] = True
    if token:
        prove_webhook(token, subscription["secret"], event, delivered)
        evidence["automaticSignedWebhook"] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubernetes", action="store_true", help="use the guarded k3d assessment namespace and localhost port-forward")
    parser.add_argument("--webhook-site", action="store_true", help="opt in to external synthetic webhook verification")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = {"result": "failed", "automaticSignedWebhook": False}
    try:
        if args.kubernetes:
            fixture.use_kubernetes()
        with receiver(args.webhook_site) as token:
            scenario(token, evidence)
        evidence["result"] = "passed" if args.webhook_site else "local-only"
    except ReceiverFailure as error:
        evidence.update(result="external-service-failed", error=str(error))
    except ApiFailure as error:
        evidence.update(result="application-request-failed", error=str(error))
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        evidence.update(error=str(error) if isinstance(error, RuntimeError) else type(error).__name__)
    finally:
        args.output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(evidence, indent=2))
    return 0 if evidence["result"] in ("passed", "local-only") else 1


if __name__ == "__main__":
    raise SystemExit(main())