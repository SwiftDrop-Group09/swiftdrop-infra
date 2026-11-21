"""Measure the isolated Compose assessment stack with synthetic users and parcels."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
import platform
import secrets
from threading import Barrier
import time
from urllib.request import Request, urlopen

from assessment_fixture import ApiFailure, ORIGIN, prepare_fixture, request

USERS = 50
POLL_SECONDS = 4
TRACKING_TRIALS = 5


def metrics(samples):
    ordered = sorted(samples)

    def percentile(fraction):
        return round(ordered[math.ceil(len(ordered) * fraction) - 1], 2)

    return {"samples": len(ordered), "p50Ms": percentile(.5), "p95Ms": percentile(.95),
            "maxMs": percentile(1)} if ordered else {"samples": 0}


def measure_load(tokens, quote, iterations):
    start_gate = Barrier(len(tokens) + 1)

    def worker(token):
        start_gate.wait(timeout=20)
        outcomes = []
        for _ in range(iterations):
            for label, path, body in (
                ("readSummary", "/api/v1/parcels/summary", None),
                ("writeQuote", "/api/v1/quotes", quote),
            ):
                start = time.perf_counter()
                error_code = None
                try:
                    request(path, body, token)
                except ApiFailure as error:
                    error_code = str(error.status)
                except OSError:
                    error_code = "transport"
                outcomes.append((label, (time.perf_counter() - start) * 1000, error_code))
        return outcomes

    with ThreadPoolExecutor(max_workers=len(tokens)) as pool:
        work = [pool.submit(worker, token) for token in tokens]
        start = time.perf_counter()
        start_gate.wait(timeout=20)
        outcomes = [sample for result in work for sample in result.result()]
        duration = time.perf_counter() - start

    profiles = {}
    for label in ("readSummary", "writeQuote"):
        samples = [sample for sample in outcomes if sample[0] == label]
        errors = {}
        for _, _, error in samples:
            if error:
                errors[error] = errors.get(error, 0) + 1
        profiles[label] = {
            **metrics([elapsed for _, elapsed, error in samples if not error]),
            "failures": errors,
        }
    return {
        "concurrentUsers": len(tokens), "requests": len(outcomes),
        "requestsPerSecond": round(len(outcomes) / duration, 2), "profiles": profiles,
    }


def read_snapshot(stream, expected_status):
    while line := stream.readline():
        if line.startswith(b"data: "):
            record = json.loads(line[6:])
            if record["status"] == expected_status:
                return record
    raise OSError("Tracking connection ended before the expected snapshot")


def arrival_ms(snapshot, observed):
    changed = datetime.fromisoformat(snapshot["lastUpdatedAt"]).timestamp()
    elapsed = (observed - changed) * 1000
    if elapsed < 0:
        raise ValueError("Client and database clocks are not synchronized")
    return elapsed


def measure_tracking(tokens, admin_token, courier_id, quote):
    samples = {"sse": [], "polling": []}
    misses = {"sse": 0, "polling": 0}
    for customer_token in tokens[:TRACKING_TRIALS]:
        accepted = request("/api/v1/quotes", quote, customer_token)
        parcel = request("/api/v1/parcels", {
            "quoteId": accepted["quoteId"], "acceptTotal": accepted["total"],
        }, customer_token, idempotency_key=secrets.token_hex(12))
        path = "/api/v1/parcels/track/" + parcel["trackingNumber"]
        stream_request = Request(ORIGIN + path + "/events", headers={"Accept": "text/event-stream"})

        def poll():
            # Measure a transition just after a REST tick, the slow end of the
            # browser's four-second polling phase.
            for _ in range(3):
                time.sleep(POLL_SECONDS)
                snapshot = request(path)
                if snapshot["status"] == "Assigned":
                    return arrival_ms(snapshot, time.time())
            raise OSError("Polling did not observe the assigned parcel")

        with urlopen(stream_request, timeout=10) as stream, ThreadPoolExecutor(max_workers=1) as pool:
            if stream.headers.get_content_type() != "text/event-stream":
                raise OSError("Gateway did not return an event stream")
            read_snapshot(stream, "Created")
            if request(path)["status"] != "Created":
                raise OSError("Unexpected initial REST tracking state")
            polling = pool.submit(poll)
            request("/api/v1/deliveries/assign", {
                "parcelId": parcel["id"], "courierId": courier_id, "expectedVersion": parcel["version"],
            }, admin_token, idempotency_key=secrets.token_hex(12))
            try:
                samples["sse"].append(arrival_ms(read_snapshot(stream, "Assigned"), time.time()))
            except (OSError, ValueError):
                misses["sse"] += 1
            try:
                samples["polling"].append(polling.result())
            except (OSError, ValueError, ApiFailure):
                misses["polling"] += 1
    return {
        mode: {**metrics(values), "missed": misses[mode]}
        for mode, values in samples.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=3, choices=range(1, 21))
    args = parser.parse_args()
    tokens, admin_token, courier_id, _, quote = prepare_fixture(USERS)
    if len(set(tokens)) != USERS:
        raise SystemExit("Expected a distinct session for every measured user.")
    load = measure_load(tokens, quote, args.iterations)
    tracking = measure_tracking(tokens, admin_token, courier_id, quote)
    results = {
        "environment": {"target": "local Compose assessment web proxy",
                        "clientPlatform": platform.system(), "iterationsPerUser": args.iterations,
                        "pollIntervalSeconds": POLL_SECONDS, "trackingTrials": TRACKING_TRIALS},
        "load": load, "tracking": tracking,
        "targets": {
            "50ConcurrentUsers": load["concurrentUsers"] == USERS and bool(load["requests"])
                and all(not profile["failures"] for profile in load["profiles"].values()),
            "apiP95Under500Ms": all(profile.get("p95Ms", math.inf) <= 500
                                    for profile in load["profiles"].values()),
            "trackingUnder2000Ms": {
                mode: metrics.get("maxMs", math.inf) <= 2000 and not metrics["missed"]
                for mode, metrics in tracking.items()
            },
        },
    }
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
