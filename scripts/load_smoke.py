"""Bounded HTTP load smoke test for a running local or staging API."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _request(url: str, timeout: float) -> tuple[int, float, str | None]:
    started = time.perf_counter()
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "pdm-load-smoke/1"})
        with urlopen(request, timeout=timeout) as response:
            response.read()
            return int(response.status), time.perf_counter() - started, None
    except HTTPError as exc:
        return int(exc.code), time.perf_counter() - started, f"http_{exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        return 0, time.perf_counter() - started, type(exc).__name__


def run_load(url: str, requests: int = 50, concurrency: int = 5, timeout: float = 5.0,
             expected_status: int = 200, p95_limit: float = 2.0) -> dict[str, object]:
    if requests < 1 or requests > 10_000:
        raise ValueError("requests must be between 1 and 10000")
    if concurrency < 1 or concurrency > 100:
        raise ValueError("concurrency must be between 1 and 100")
    durations: list[float] = []
    statuses: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    with ThreadPoolExecutor(max_workers=min(concurrency, requests)) as pool:
        futures = [pool.submit(_request, url, timeout) for _ in range(requests)]
        for future in as_completed(futures):
            status, duration, error = future.result()
            durations.append(duration)
            statuses[str(status)] += 1
            if status != expected_status:
                errors[error or f"unexpected_status_{status}"] += 1
    p95 = _percentile(durations, 0.95)
    return {
        "status": "PASS" if not errors and p95 <= p95_limit else "FAIL",
        "url": url,
        "requests": requests,
        "concurrency": concurrency,
        "expected_status": expected_status,
        "status_counts": dict(statuses),
        "error_counts": dict(errors),
        "latency_seconds": {"p50": round(_percentile(durations, 0.50), 6), "p95": round(p95, 6), "max": round(max(durations, default=0.0), 6)},
        "evidence_class": "REFERENCE_BOUNDED_HTTP_LOAD_SMOKE_NOT_CAPACITY_CERTIFICATION",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a bounded API load smoke test")
    parser.add_argument("--url", default="http://127.0.0.1:8001/api/health")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--p95-limit", type=float, default=2.0)
    parser.add_argument("--output", default="artifacts/load_smoke.json")
    args = parser.parse_args()
    result = run_load(args.url, args.requests, args.concurrency, args.timeout, p95_limit=args.p95_limit)
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
