"""Read-only evaluation of a potential KR direct-traffic anomaly."""

from collections.abc import Mapping
from datetime import date, timedelta
from statistics import median


def kr_direct_candidate(
    *,
    snap_date: date,
    visits: int,
    sample_interval: float | None,
    previous_visits: Mapping[date, int],
    countries: Mapping[str, tuple[int, float | None]],
    referrers: Mapping[str, tuple[int, float | None]],
) -> int | None:
    """Return the minimum KR direct count for a review candidate, if any.

    Each dimension maps a stored key to its (visits, sample_interval) on the
    same UTC day. History contains raw daily visit totals keyed by UTC date.
    """
    if visits < 500 or sample_interval != 1:
        return None

    for dimension in (countries, referrers):
        if any(interval != 1 for _, interval in dimension.values()):
            return None
        if sum(count for count, _ in dimension.values()) != visits:
            return None

    kr_visits, _ = countries.get("KR", (0, None))
    direct_visits, _ = referrers.get("(direct)", (0, None))
    # At most `visits` members can be in the union of these two groups.
    overlap = max(kr_visits + direct_visits - visits, 0)
    if overlap < 500 or 10 * overlap < 9 * visits:
        return None

    start = snap_date - timedelta(days=28)
    history = [count for day, count in previous_visits.items() if start <= day < snap_date]
    if len(history) < 7 or visits < 4 * median(history):
        return None

    return overlap
