#!/usr/bin/env python3
"""RUM referrer × landing path; additive collector, never writes legacy tables.

UTC, bot filter, precision bands and query-string removal come from the existing
collector. Logs contain row counts/flags only, never hosts, paths or visit values.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta

import collect_rum_analytics as rum
import grm_cli

# A separate budget, not REFERRER_CAP × PATH_CAP. One UTC day per request avoids
# a busy earlier day starving later days. 10,000 uses the existing supported API
# budget; 5,000 stored pairs/day allows more than one pair per ~4,000 site pages.
# Neither promises exhaustive coverage: both limits are recorded on every run.
API_LIMIT = 10000
DAY_CAP = 5000


def build_query(limit=API_LIMIT):
    if not 1 <= limit <= API_LIMIT:
        raise ValueError("cross API limit must be between 1 and 10000")
    return (rum._QUERY_TEMPLATE.replace("__LIMIT__", str(limit))
            .replace("__FIELDS__", "        sum { visits }")
            .replace("__DIMS__", "date refererHost requestPath")
            .replace("[date_ASC]", "[sum_visits_DESC, date_ASC, refererHost_ASC, requestPath_ASC]"))


def day_windows(start, end):
    """Keep the legacy <=3-day chunks, then narrow each to single UTC dates."""
    for ws, we, _ in rum.split_window(start, end):
        # Legacy split_window shares the midnight endpoint with the live window.
        # Narrow the completed part here; never ingest a one-second next day.
        if we.endswith('T00:00:00Z') and we > ws:
            we = (datetime.fromisoformat(we.replace('Z', '+00:00')) - timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        for cs, ce in rum.chunk_window(ws, we, rum.CHUNK_DAYS):
            cursor = datetime.fromisoformat(cs.replace("Z", "+00:00"))
            stop = datetime.fromisoformat(ce.replace("Z", "+00:00"))
            while cursor <= stop:
                midnight = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0)
                last = min(stop, midnight - timedelta(seconds=1))
                yield cursor.strftime("%Y-%m-%dT%H:%M:%SZ"), last.strftime("%Y-%m-%dT%H:%M:%SZ")
                cursor = midnight


def parse_response(payload, start, end, api_limit=API_LIMIT, day_cap=DAY_CAP):
    if not 1 <= day_cap <= DAY_CAP:
        raise ValueError("cross day cap must be between 1 and 5000")
    # Do not print GraphQL errors: a server may echo a value in an error message.
    if payload.get("errors"):
        raise ValueError("cross GraphQL errors present (details suppressed)")
    accounts = ((payload.get("data") or {}).get("viewer") or {}).get("accounts")
    if not accounts or not isinstance(accounts[0].get("rows"), list):
        raise ValueError("cross response missing rows")
    raw = accounts[0]["rows"]
    pairs, sample = {}, 1.0
    for row in raw:
        dims = row.get("dimensions") or {}
        if dims.get("date") != start[:10] or not {"refererHost", "requestPath"} <= dims.keys():
            raise ValueError("cross response dimensions invalid")
        sample = max(sample, rum.sample_interval_of(row, "referrer_paths"))
        visits = (row.get("sum") or {}).get("visits")
        if not isinstance(visits, int) or isinstance(visits, bool) or visits < 0:
            raise ValueError("cross visits invalid")
        if not visits:
            continue
        pair = (str(dims["refererHost"] or "").strip() or "(direct)",
                rum.clean_path(dims["requestPath"]))
        pairs[pair] = pairs.get(pair, 0) + visits
    ordered = sorted(pairs.items(), key=lambda p: (-p[1], p[0]))
    run = dict(snap_date=start[:10], window_start=start, window_end=end,
               api_limit=api_limit, day_cap=day_cap, received_rows=len(raw),
               retained_rows=min(len(ordered), day_cap), dropped_pairs=max(0, len(ordered)-day_cap),
               api_limit_hit=len(raw) >= api_limit, day_cap_hit=len(ordered) > day_cap,
               sample_interval=sample)
    rows = [dict(snap_date=start[:10], referer_host=host, request_path=path,
                 visits=visits, sample_interval=sample)
            for (host, path), visits in ordered[:day_cap]]
    return rows, run


def should_replace(run, old, allow_downgrade=False):
    """Same precision ratchet; also never replace complete data with a cut sample."""
    if not old:
        return True
    if run["window_end"] < old["window_end"]:
        return False
    if (run["api_limit_hit"] or run["day_cap_hit"]) and not (old["api_limit_hit"] or old["day_cap_hit"]):
        return False
    write, _ = rum.keep_days({run["snap_date"]: run["sample_interval"]},
                            {run["snap_date"]: old["sample_interval"]},
                            allow_downgrade=allow_downgrade)
    return bool(write)


def save(url, key, rows, run, allow_downgrade=False):
    """Atomic RPC rechecks the ratchet under a per-day lock and records all attempts.

    No delete+insert REST gap; even a rejected/truncated empty attempt has a row in
    rum_referrer_path_runs. No existing table is read or written by this RPC.
    """
    import requests
    base, headers = rum._rest(url, key)
    response = requests.post(base + "/rest/v1/rpc/store_rum_referrer_paths",
                             headers=headers, timeout=30,
                             json={"p_rows": rows, "p_run": run,
                                   "p_allow_downgrade": allow_downgrade})
    if response.status_code >= 300:
        raise RuntimeError(f"cross store HTTP {response.status_code} (details suppressed)")
    return response.json()


def main(argv=None):
    import requests
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--api-limit", type=int, default=API_LIMIT)
    ap.add_argument("--day-cap", type=int, default=DAY_CAP)
    ap.add_argument("--allow-downgrade", action="store_true")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--service-role-key", default=None)
    args = ap.parse_args(argv)
    query = build_query(args.api_limit)
    if not 1 <= args.day_cap <= DAY_CAP:
        ap.error("day-cap outside supported range")
    for stamp in (args.start, args.end):
        datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    if args.start > args.end or args.start[11:] != "00:00:00Z":
        ap.error("start must be UTC midnight and not after end")
    token = os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN", "").strip()
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    site = os.environ.get("CLOUDFLARE_RUM_SITE_TAG", "").strip()
    if not token or not account or not site:
        ap.error("Cloudflare analytics credentials/identifiers missing")
    creds = None if args.dry_run else grm_cli.resolve_supabase_service_credentials(args)
    if not args.dry_run and not creds:
        ap.error("Supabase service credentials missing")
    for start, end in day_windows(args.start, args.end):
        response = requests.post(rum.GRAPHQL_ENDPOINT, timeout=30,
                                 headers={"Authorization": "Bearer " + token},
                                 json={"query": query, "variables": {"accountTag": account,
                                       "siteTag": site, "start": start, "end": end}})
        if response.status_code >= 300:
            raise RuntimeError(f"cross GraphQL HTTP {response.status_code} (details suppressed)")
        rows, run = parse_response(response.json(), start, end, args.api_limit, args.day_cap)
        outcome = {"stored": False, "reason": "dry-run"} if creds is None else save(
            *creds, rows, run, args.allow_downgrade)
        print(f"cross rows_received={run['received_rows']} rows_retained={len(rows)} "
              f"dropped_pairs={run['dropped_pairs']} api_limit_hit={run['api_limit_hit']} "
              f"day_cap_hit={run['day_cap_hit']} stored={outcome['stored']} reason={outcome['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
