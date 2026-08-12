#!/usr/bin/env python3
"""Standalone API check: You.com + Yep.com web search (search only, NO extraction).

Verified against official docs (2026-08-12):
  You: https://you.com/docs/quickstart  -> POST https://ydc-index.io/v1/search, X-API-Key
  Yep: https://platform.yep.com/api-documentation -> POST https://platform.yep.com/api/search, Bearer

Reads HYPERION_YOU_API_KEY / HYPERION_YEP_API_KEY straight from the repo .env,
fires a real search at each provider, prints HTTP status + top results.
Pure stdlib (urllib) — zero Hyperion code, zero third-party deps.

Usage:  python scripts/check_you_yep_search.py ["custom query"]
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

QUERY = "top 10 fastest blockchain with highest tps"
UA = "hyperion-api-check/1.0 (standalone; search only)"


def load_env(path: str) -> dict[str, str]:
    """Minimal .env parser — KEY=VALUE, skips comments/blank lines."""
    env: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                env[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        print(f"  ! .env not found at {path}")
    return env


def redact(key: str) -> str:
    if not key:
        return "<MISSING>"
    return f"{key[:6]}...{key[-4:]} (len={len(key)})" if len(key) > 12 else "<too-short?>"


def http_json(method: str, url: str, headers: dict, body: bytes | None = None, timeout: int = 25):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    elapsed = time.monotonic() - t0
    return resp.status, elapsed, json.loads(raw.decode("utf-8", "replace"))


def check_you(key: str) -> None:
    print(f"[You.com]   key={redact(key)}")
    if not key:
        print("  SKIP - no HYPERION_YOU_API_KEY in .env\n")
        return
    # Verified against https://you.com/docs/quickstart (2026-08-12)
    url = "https://ydc-index.io/v1/search"
    payload = json.dumps({"query": QUERY, "count": 10}).encode("utf-8")
    headers = {"X-API-Key": key, "Content-Type": "application/json", "User-Agent": UA}
    try:
        status, elapsed, data = http_json("POST", url, headers, payload)
        results = (data.get("results") or {}).get("web") or []
        print(f"  HTTP {status} in {elapsed:.2f}s | web results={len(results)}")
        for i, r in enumerate(results[:10], 1):
            title = r.get("title", "")
            snips = r.get("snippets") or [r.get("description", "")]
            print(f"   {i:>2}. {title}")
            print(f"       {r.get('url', '')}")
            print(f"       {str(snips[0])[:150] if snips else ''}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} ERROR: {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {type(e).__name__}: {e}")
    print()


def check_yep(key: str) -> None:
    print(f"[Yep.com]   key={redact(key)}")
    if not key:
        print("  SKIP - no HYPERION_YEP_API_KEY in .env\n")
        return
    # Verified against https://platform.yep.com/api-documentation (2026-08-12)
    url = "https://platform.yep.com/api/search"
    payload = json.dumps({
        "query": QUERY,
        "type": "basic",
        "limit": 10,
        "language": ["en"],
        "location": "US",
    }).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": UA}
    try:
        status, elapsed, data = http_json("POST", url, headers, payload)
        results = data.get("results") or []
        print(f"  HTTP {status} in {elapsed:.2f}s | results={len(results)} | cost={data.get('api_cost')} | bal={data.get('balance')}")
        for i, r in enumerate(results[:10], 1):
            title = r.get("title", "") if isinstance(r, dict) else str(r)
            u = r.get("url", "") if isinstance(r, dict) else ""
            print(f"   {i:>2}. {title}")
            print(f"       {u}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} ERROR: {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {type(e).__name__}: {e}")
    print()


def main() -> None:
    global QUERY
    if len(sys.argv) > 1 and sys.argv[1].strip():
        QUERY = sys.argv[1].strip()

    # repo root = parent of scripts/
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    env = load_env(os.path.join(root, ".env"))

    print(f"Query: {QUERY!r}\n")
    check_you(env.get("HYPERION_YOU_API_KEY", ""))
    check_yep(env.get("HYPERION_YEP_API_KEY", ""))


if __name__ == "__main__":
    main()
