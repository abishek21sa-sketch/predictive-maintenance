"""Dependency-free runtime telemetry for the API process.

This module deliberately keeps only low-cardinality operational counters in
memory. It is a local instrumentation boundary, not a replacement for the
managed metrics, logs, and traces required by a production deployment.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

# Stable buckets support latency alerting without creating high-cardinality
# dimensions in the metrics backend.
_LATENCY_BUCKETS: tuple[float, ...] = (
    0.005,
    0.025,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)
_SAFE_LABEL_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_{}./-"
)
_MAX_LABEL_LENGTH = 160


def _bounded_label(value: object, fallback: str) -> str:
    text = str(value)
    if (
        not text
        or len(text) > _MAX_LABEL_LENGTH
        or any(character not in _SAFE_LABEL_CHARS for character in text)
    ):
        return fallback
    return text


@dataclass
class RuntimeMetrics:
    """Thread-safe request counters suitable for a Prometheus scrape."""

    started_at: float = field(default_factory=monotonic)
    _lock: Lock = field(default_factory=Lock, repr=False)
    _requests: dict[tuple[str, str, str], int] = field(
        default_factory=lambda: defaultdict(int), repr=False
    )
    _duration_seconds: dict[tuple[str, str], float] = field(
        default_factory=lambda: defaultdict(float), repr=False
    )
    _duration_count: dict[tuple[str, str], int] = field(
        default_factory=lambda: defaultdict(int), repr=False
    )
    _duration_buckets: dict[tuple[str, str, float], int] = field(
        default_factory=lambda: defaultdict(int), repr=False
    )
    _server_errors: dict[tuple[str, str, str], int] = field(
        default_factory=lambda: defaultdict(int), repr=False
    )
    _in_flight: int = field(default=0, repr=False)

    def request_started(self) -> None:
        with self._lock:
            self._in_flight += 1

    def request_finished(self, method: str, route: str, status: int, duration_seconds: float) -> None:
        safe_method = _bounded_label(str(method).upper(), "UNKNOWN")
        safe_route = _bounded_label(route, "/unclassified")
        status_value = int(status)
        safe_status = status_value if 100 <= status_value <= 599 else 0
        status_label = str(safe_status) if safe_status else "other"
        status_class = f"{safe_status // 100}xx" if safe_status else "other"
        key = (safe_method, safe_route)
        duration = max(0.0, duration_seconds)
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
            self._requests[(safe_method, safe_route, status_label)] += 1
            self._duration_seconds[key] += duration
            self._duration_count[key] += 1
            for bucket in _LATENCY_BUCKETS:
                if duration <= bucket:
                    self._duration_buckets[(safe_method, safe_route, bucket)] += 1
            self._duration_buckets[(safe_method, safe_route, float("inf"))] += 1
            if safe_status >= 500:
                self._server_errors[(safe_method, safe_route, status_class)] += 1

    def prometheus(self) -> str:
        """Render stable, low-cardinality metrics in Prometheus text format."""

        with self._lock:
            requests = dict(self._requests)
            durations = dict(self._duration_seconds)
            duration_counts = dict(self._duration_count)
            duration_buckets = dict(self._duration_buckets)
            server_errors = dict(self._server_errors)
            in_flight = self._in_flight
            uptime = max(0.0, monotonic() - self.started_at)

        lines = [
            "# HELP pdm_http_requests_total HTTP requests handled by the API.",
            "# TYPE pdm_http_requests_total counter",
        ]
        for (method, route, status), count in sorted(requests.items()):
            lines.append(
                f'pdm_http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}'
            )

        lines.extend(
            [
                "# HELP pdm_http_server_errors_total HTTP responses with a 5xx status.",
                "# TYPE pdm_http_server_errors_total counter",
            ]
        )
        for (method, route, status_class), count in sorted(server_errors.items()):
            lines.append(
                f'pdm_http_server_errors_total{{method="{method}",route="{route}",status_class="{status_class}"}} {count}'
            )

        lines.extend(
            [
                "# HELP pdm_http_request_duration_seconds Request duration histogram.",
                "# TYPE pdm_http_request_duration_seconds histogram",
            ]
        )
        for method, route in sorted(durations):
            for bucket in _LATENCY_BUCKETS:
                bucket_label = f"{bucket:g}"
                count = duration_buckets.get((method, route, bucket), 0)
                lines.append(
                    f'pdm_http_request_duration_seconds_bucket{{method="{method}",route="{route}",le="{bucket_label}"}} {count}'
                )
            count = duration_buckets.get((method, route, float("inf")), 0)
            lines.append(
                f'pdm_http_request_duration_seconds_bucket{{method="{method}",route="{route}",le="+Inf"}} {count}'
            )

        lines.extend(
            [
                "# HELP pdm_http_request_duration_seconds_sum HTTP request duration sum.",
                "# TYPE pdm_http_request_duration_seconds_sum counter",
            ]
        )
        for (method, route), duration in sorted(durations.items()):
            lines.append(
                f'pdm_http_request_duration_seconds_sum{{method="{method}",route="{route}"}} '
                f"{duration:.9f}"
            )

        lines.extend(
            [
                "# HELP pdm_http_request_duration_seconds_count HTTP request duration count.",
                "# TYPE pdm_http_request_duration_seconds_count counter",
            ]
        )
        for (method, route), count in sorted(duration_counts.items()):
            lines.append(
                f'pdm_http_request_duration_seconds_count{{method="{method}",route="{route}"}} {count}'
            )

        lines.extend(
            [
                "# HELP pdm_http_in_flight_requests Requests currently being handled.",
                "# TYPE pdm_http_in_flight_requests gauge",
                f"pdm_http_in_flight_requests {in_flight}",
                "# HELP pdm_process_uptime_seconds API process uptime.",
                "# TYPE pdm_process_uptime_seconds gauge",
                f"pdm_process_uptime_seconds {uptime:.3f}",
                "",
            ]
        )
        return "\n".join(lines)


runtime_metrics = RuntimeMetrics()
