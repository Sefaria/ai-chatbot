"""
Load test script for the chat streaming endpoint.

Sends concurrent requests with isLoadTest=True, which:
- Routes to a cheaper model (LOAD_TEST_MODEL, defaults to claude-haiku)
- Disables Braintrust logging

Usage:
    # From server/ directory (uses CHATBOT_API_BASE_URL env var):
    python -m loadtest.load_test --users 5 --requests 10

    # Override URL explicitly:
    python -m loadtest.load_test --url http://localhost:8001/api --users 10 --requests 20

    # Quick smoke test (1 request):
    python -m loadtest.load_test --users 1 --requests 1 --verbose

    # Find capacity limit (step-up mode):
    python -m loadtest.load_test --step-up
    python -m loadtest.load_test --step-up --p95-limit 30000 --error-limit 5
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------------------
# Token generation (mirrors user_token_service.py)
# ---------------------------------------------------------------------------

NONCE_SIZE_BYTES = 12


def _derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def generate_user_token(user_id: str, secret: str, ttl_hours: int = 1) -> str:
    """Generate a valid encrypted user token for the given user_id."""
    expiration = (datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat()
    payload = json.dumps({"id": user_id, "expiration": expiration}).encode("utf-8")

    nonce = os.urandom(NONCE_SIZE_BYTES)
    aesgcm = AESGCM(_derive_key(secret))
    encrypted = aesgcm.encrypt(nonce, payload, None)

    token_bytes = nonce + encrypted
    return base64.urlsafe_b64encode(token_bytes).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Single request
# ---------------------------------------------------------------------------

QUESTIONS = [
    "What is the Shema?",
    "Tell me about Shabbat",
    "What does the Torah say about kindness?",
    "Who was Rabbi Akiva?",
    "What is Talmud?",
    "Explain the concept of teshuvah",
    "What are the Ten Commandments?",
    "Tell me about Passover",
]


def run_single_request(
    url: str,
    user_token: str,
    session_id: str,
    question: str,
    is_load_test: bool = True,
    timeout: int = 120,
    verbose: bool = False,
) -> dict:
    """
    Send one streaming request and return timing + outcome info.

    Returns a dict with:
        success: bool
        latency_ms: int        # time to first SSE event
        total_ms: int          # total wall time including full response
        status_code: int | None
        error: str | None
        response_preview: str  # first 100 chars of final message
    """
    start = time.monotonic()
    first_event_ms = None
    response_preview = ""
    error = None
    status_code = None

    payload = {
        "userId": user_token,
        "sessionId": session_id,
        "messageId": str(uuid.uuid4()),
        "timestamp": datetime.now(UTC).isoformat(),
        "text": question,
        "isLoadTest": is_load_test,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", f"{url}/v2/chat/stream", json=payload) as resp:
                status_code = resp.status_code
                if status_code != 200:
                    error = f"HTTP {status_code}"
                    return {
                        "success": False,
                        "latency_ms": int((time.monotonic() - start) * 1000),
                        "total_ms": int((time.monotonic() - start) * 1000),
                        "status_code": status_code,
                        "error": error,
                        "response_preview": "",
                    }

                for line in resp.iter_lines():
                    if first_event_ms is None and line.strip():
                        first_event_ms = int((time.monotonic() - start) * 1000)

                    if not line.startswith("data:"):
                        continue

                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue

                    if verbose:
                        event_type = data.get("type", "message")
                        text = data.get("text", data.get("markdown", ""))
                        print(f"  [{event_type}] {str(text)[:80]}")

                    if "markdown" in data:
                        response_preview = data["markdown"][:100]

    except httpx.TimeoutException:
        error = f"timeout after {timeout}s"
    except Exception as exc:
        error = str(exc)

    total_ms = int((time.monotonic() - start) * 1000)
    return {
        "success": error is None,
        "latency_ms": first_event_ms or total_ms,
        "total_ms": total_ms,
        "status_code": status_code,
        "error": error,
        "response_preview": response_preview,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_load_test(
    url: str,
    secret: str,
    concurrency: int,
    total_requests: int,
    timeout: int,
    verbose: bool,
    is_load_test: bool = True,
) -> dict:
    wall_start = time.monotonic()
    flag_label = (
        "isLoadTest=true (haiku, no Braintrust)"
        if is_load_test
        else "isLoadTest=false (sonnet, Braintrust enabled)"
    )
    print(f"\nLoad test: {total_requests} requests, {concurrency} concurrent")
    print(f"Target:    {url}")
    print(f"Flag:      {flag_label}")
    print("-" * 60)

    # Build work items: each gets its own session so requests are independent
    items = []
    for i in range(total_requests):
        user_id = f"loadtest-user-{i % concurrency}"
        user_token = generate_user_token(user_id, secret)
        session_id = str(uuid.uuid4())
        question = QUESTIONS[i % len(QUESTIONS)]
        items.append((user_token, session_id, question))

    results = []
    completed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(run_single_request, url, token, sess, q, is_load_test, timeout, verbose): (
                i,
                q,
            )
            for i, (token, sess, q) in enumerate(items)
        }
        for future in as_completed(futures):
            idx, question = futures[future]
            result = future.result()
            results.append(result)
            completed += 1
            status = "OK" if result["success"] else f"FAIL ({result['error']})"
            print(
                f"  [{completed:3d}/{total_requests}] {status:30s}  total={result['total_ms']}ms  q={question[:40]!r}"
            )

    wall_seconds = time.monotonic() - wall_start

    # Statistics
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]
    latencies = [r["total_ms"] for r in successes]

    print("\n" + "=" * 60)
    print(f"Results: {len(successes)}/{total_requests} succeeded")
    if failures:
        for f in failures:
            print(f"  FAILED: {f['error']}")

    p95 = None
    if latencies:
        p95 = statistics.quantiles(latencies, n=100)[94] if len(latencies) >= 2 else latencies[0]
        rps = total_requests / wall_seconds if wall_seconds > 0 else 0
        print("\nLatency (total wall time):")
        print(f"  min:    {min(latencies)}ms")
        print(f"  median: {statistics.median(latencies):.0f}ms")
        print(f"  p95:    {p95}ms")
        print(f"  max:    {max(latencies)}ms")
        print(f"  mean:   {statistics.mean(latencies):.0f}ms")
        print(f"\nThroughput: {rps:.2f} req/s  (wall time: {wall_seconds:.1f}s)")
    print()

    error_pct = (len(failures) / total_requests * 100) if total_requests else 100
    return {
        "successes": len(successes),
        "failures": len(failures),
        "p95": p95,
        "error_pct": error_pct,
    }


# ---------------------------------------------------------------------------
# Step-up capacity finder
# ---------------------------------------------------------------------------

STEP_UP_LEVELS = [1, 2, 5, 10, 20, 50, 100]


def run_step_up_test(
    url: str,
    secret: str,
    timeout: int,
    p95_limit_ms: int,
    error_limit_pct: float,
    requests_per_level: int,
    is_load_test: bool = True,
) -> None:
    """
    Escalate concurrency level-by-level until the system degrades.

    Degradation is defined as either:
    - p95 latency exceeds p95_limit_ms, OR
    - error rate exceeds error_limit_pct %

    Prints a capacity summary at the end.
    """
    print("\n" + "#" * 60)
    print("# STEP-UP CAPACITY TEST")
    print(f"# SLA: p95 < {p95_limit_ms}ms, error rate < {error_limit_pct}%")
    print(f"# Levels: {STEP_UP_LEVELS}")
    print("#" * 60)

    last_healthy = None
    results_by_level: list[dict] = []

    for concurrency in STEP_UP_LEVELS:
        n = max(concurrency * 3, requests_per_level)
        print(f"\n>>> Level: {concurrency} concurrent users ({n} total requests)")
        stats = run_load_test(
            url=url,
            secret=secret,
            concurrency=concurrency,
            total_requests=n,
            timeout=timeout,
            verbose=False,
            is_load_test=is_load_test,
        )

        healthy = True
        reasons = []
        if stats["p95"] is not None and stats["p95"] > p95_limit_ms:
            healthy = False
            reasons.append(f"p95 {stats['p95']}ms > limit {p95_limit_ms}ms")
        if stats["error_pct"] > error_limit_pct:
            healthy = False
            reasons.append(f"error rate {stats['error_pct']:.1f}% > limit {error_limit_pct}%")

        status = "HEALTHY" if healthy else f"DEGRADED ({', '.join(reasons)})"
        results_by_level.append({"concurrency": concurrency, "healthy": healthy, **stats})
        print(f"    => {status}")

        if healthy:
            last_healthy = concurrency
        else:
            print(f"\n>>> System degraded at {concurrency} concurrent users. Stopping.")
            break

    print("\n" + "=" * 60)
    print("CAPACITY SUMMARY")
    print("=" * 60)
    print(f"{'Users':>6}  {'p95 (ms)':>10}  {'Error %':>8}  {'Status'}")
    print("-" * 60)
    for r in results_by_level:
        p95_str = str(r["p95"]) if r["p95"] is not None else "N/A"
        status = "OK" if r["healthy"] else "FAIL"
        print(f"  {r['concurrency']:>4}  {p95_str:>10}  {r['error_pct']:>7.1f}%  {status}")

    print()
    if last_healthy:
        print(f"  Estimated capacity: ~{last_healthy} concurrent users")
        failing = [r for r in results_by_level if not r["healthy"]]
        if failing:
            print(f"  System degrades at: {failing[0]['concurrency']} concurrent users")
    else:
        print("  System was degraded even at 1 concurrent user.")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat API load test (isLoadTest=true)")
    parser.add_argument(
        "--url",
        default=os.environ.get("CHATBOT_API_BASE_URL", "http://localhost:8001/api"),
        help="Base API URL of the server (default: CHATBOT_API_BASE_URL env var or http://localhost:8001/api)",
    )
    parser.add_argument("--users", type=int, default=5, help="Concurrent users")
    parser.add_argument("--requests", type=int, default=10, help="Total requests to send")
    parser.add_argument("--timeout", type=int, default=120, help="Per-request timeout in seconds")
    parser.add_argument(
        "--secret",
        default=os.environ.get("CHATBOT_USER_ID_SECRET", "secret"),
        help="Token secret (default: CHATBOT_USER_ID_SECRET env var or 'secret')",
    )
    parser.add_argument("--verbose", action="store_true", help="Print SSE events as they arrive")
    parser.add_argument(
        "--no-load-test",
        dest="load_test",
        action="store_false",
        help="Send isLoadTest=false (uses sonnet model + Braintrust)",
    )
    # Step-up capacity finder
    parser.add_argument(
        "--step-up",
        action="store_true",
        help="Run escalating concurrency levels to find capacity limit",
    )
    parser.add_argument(
        "--p95-limit",
        type=int,
        default=30000,
        help="Step-up SLA: max acceptable p95 latency in ms (default: 30000)",
    )
    parser.add_argument(
        "--error-limit",
        type=float,
        default=5.0,
        help="Step-up SLA: max acceptable error rate in %% (default: 5.0)",
    )
    parser.add_argument(
        "--requests-per-level",
        type=int,
        default=10,
        help="Minimum requests per step-up level (actual = max(concurrency*3, this), default: 10)",
    )
    parser.set_defaults(load_test=True)
    args = parser.parse_args()

    if args.step_up:
        run_step_up_test(
            url=args.url,
            secret=args.secret,
            timeout=args.timeout,
            p95_limit_ms=args.p95_limit,
            error_limit_pct=args.error_limit,
            requests_per_level=args.requests_per_level,
            is_load_test=args.load_test,
        )
    else:
        run_load_test(
            url=args.url,
            secret=args.secret,
            concurrency=args.users,
            total_requests=args.requests,
            timeout=args.timeout,
            verbose=args.verbose,
            is_load_test=args.load_test,
        )


if __name__ == "__main__":
    main()
