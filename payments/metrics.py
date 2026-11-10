"""
Lightweight metrics wrapper. Uses prometheus_client if available, otherwise no-ops.
Add counters using `increment_counter(name, labels)`.
"""
try:
    from prometheus_client import Counter, Histogram
    _HAS_PROM = True
except Exception:
    _HAS_PROM = False

_counters = {}


def increment_counter(name: str, amount: int = 1, labels: dict | None = None):
    if not _HAS_PROM:
        return
    if labels:
        # label names sorted to keep key stable
        key = (name, tuple(sorted(labels.items())))
    else:
        key = (name, None)
    if key not in _counters:
        if labels:
            c = Counter(name, name, list(labels.keys()))
            _counters[key] = c.labels(**labels)
        else:
            c = Counter(name, name)
            _counters[key] = c
    _counters[key].inc(amount)


def observe_histogram(name: str, value: float, labels: dict | None = None):
    if not _HAS_PROM:
        return
    # Not implementing per-label histogram caching for simplicity
    h = Histogram(name, name)
    h.observe(value)
