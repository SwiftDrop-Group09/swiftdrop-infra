# Local assessment performance

Start the isolated Compose assessment stack and run the benchmark from the
infrastructure repository with Python 3.11 or later.

```sh
sh development/assessment.sh up
python scripts/measure-assessment.py --output assessment-performance.json
```

The benchmark checks that the assessment web container owns the loopback target.
It creates unique synthetic accounts, a zone, pricing, quotes and parcels. Use a
disposable assessment volume if you want to discard the generated records. The
fixture setup respects the login rate limit and is outside the timed window.

The 50 Customer sessions start together and each alternates authenticated
delivery summary reads with quote writes. A separate Admin and Courier fixture
drive five parcel assignments. The public SSE and REST readers watch the same
assignment through the local Nginx gateway path. Latency uses the server status
timestamp recorded during the transaction and the client receipt time. The REST
test starts immediately after a poll to measure the slow end of the browser's
four-second polling interval.

## Recorded run

The client ran on Windows with Docker Desktop Engine 29.5.2 and 16 CPUs exposed
to the engine. The local web, Gateway and PostgreSQL containers shared the
assessment Compose network. This was a three-iteration concurrency burst.
See the [sanitized JSON result](assessment-performance.json).

| Target | Result | Outcome |
| --- | --- | --- |
| 50 concurrent users | 300 requests at 581.49 requests/s, zero failures | Met |
| API p95 within 500 ms | 156.88 ms summary reads, 155.38 ms quote writes | Met |
| SSE update within 2 s | 972.86 ms p50, 987.64 ms p95 and max, zero misses | Met |
| Polling update within 2 s | 3,986.81 ms p50, 4,017.91 ms p95 and max, zero misses | Missed |

Run a longer workload on a representative staging stack before using the
throughput number for capacity planning. To bring fallback below two seconds,
test a shorter polling interval against the ingress request budget and repeat the
tracking benchmark at different points in the polling cycle.