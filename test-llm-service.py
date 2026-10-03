#!/usr/bin/env python3
"""Quick connectivity + chat smoke test for the HPE-vLLM endpoints.

Run from repo root (uses .env values when present, otherwise the documented defaults):

    python3 test-llm-service.py
    python3 test-llm-service.py --tier flagship
    python3 test-llm-service.py --base-url http://10.10.48.10:8000/v1 --model google/gemma-4-31b-it

Exits non-zero if any check fails. Prints per-step latency and the first 200 chars of the
streamed answer so the connection chain (TCP -> TLS -> vLLM) is easy to diagnose.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def http_get_json(url: str, timeout: float) -> tuple[int, dict | str]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        try:
            return resp.status, json.loads(body)
        except json.JSONDecodeError:
            return resp.status, body


def http_post_json(url: str, payload: dict, timeout: float, api_key: str) -> tuple[int, dict | str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        try:
            return resp.status, json.loads(body)
        except json.JSONDecodeError:
            return resp.status, body


def tcp_check(host: str, port: int, timeout: float) -> float:
    started = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout):
        return time.monotonic() - started


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def ok(msg: str) -> None:
    print(f"  [ OK ] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env")

    parser = argparse.ArgumentParser(description="HPE-vLLM connectivity test.")
    parser.add_argument(
        "--tier",
        choices=("flash", "flagship"),
        default="flash",
        help="Which tier to hit (flash=Gemma, flagship=Qwen). Default: flash.",
    )
    parser.add_argument("--base-url", help="Override LLM base URL (e.g. http://10.10.48.10:8000/v1).")
    parser.add_argument("--model", help="Override model id.")
    parser.add_argument(
        "--prompt",
        default="Responde con la palabra exacta: PONG",
        help="User prompt sent to the model. Default: PONG sanity check.",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout per call (s).")
    args = parser.parse_args()

    if args.base_url:
        base_url = args.base_url.rstrip("/")
        model = args.model or "(unspecified)"
    elif args.tier == "flagship":
        base_url = os.environ.get("LLM_FLAGSHIP_BASE_URL", "http://10.10.48.10:8001/v1").rstrip("/")
        model = args.model or os.environ.get("LLM_FLAGSHIP_MODEL", "Qwen/Qwen3-235B-A22B")
    else:
        base_url = os.environ.get("LLM_FLASH_BASE_URL", "http://10.10.48.10:8000/v1").rstrip("/")
        model = args.model or os.environ.get("LLM_FLASH_MODEL", "google/gemma-4-31b-it")

    api_key = os.environ.get("LLM_API_KEY", "not-needed") or "not-needed"

    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    print(f"Tier   : {args.tier}")
    print(f"Base   : {base_url}")
    print(f"Model  : {model}")
    print(f"Host   : {host}:{port}")
    print(f"API key: {'<set>' if api_key != 'not-needed' else 'not-needed'}")

    failures = 0

    # 1) TCP reachability.
    step("1. TCP reachability")
    try:
        elapsed = tcp_check(host, port, args.timeout)
        ok(f"connected to {host}:{port} in {elapsed * 1000:.1f} ms")
    except Exception as exc:
        fail(f"could not connect to {host}:{port} -> {exc}")
        return 1

    # 2) /v1/models (does the endpoint speak OpenAI?).
    step("2. GET /models")
    started = time.monotonic()
    try:
        status, body = http_get_json(f"{base_url}/models", args.timeout)
        elapsed = time.monotonic() - started
        ok(f"HTTP {status} in {elapsed * 1000:.1f} ms")
        if isinstance(body, dict):
            ids = [m.get("id") for m in body.get("data", [])]
            ok(f"models advertised: {ids or '<none>'}")
            if model not in ids and ids:
                fail(f"requested model {model!r} is not in advertised list")
                failures += 1
        else:
            fail(f"non-JSON body: {str(body)[:200]}")
            failures += 1
    except urllib.error.HTTPError as exc:
        fail(f"HTTP {exc.code} -> {exc.read()[:200]!r}")
        failures += 1
    except Exception as exc:
        fail(f"request failed: {exc}")
        failures += 1

    # 3) /v1/chat/completions (non-streaming).
    step("3. POST /chat/completions (non-stream)")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": args.prompt}],
        "temperature": 0.0,
        "max_tokens": 32,
    }
    started = time.monotonic()
    try:
        status, body = http_post_json(f"{base_url}/chat/completions", payload, args.timeout, api_key)
        elapsed = time.monotonic() - started
        ok(f"HTTP {status} in {elapsed * 1000:.1f} ms")
        if isinstance(body, dict):
            choices = body.get("choices") or []
            if not choices:
                fail(f"no choices in response: {json.dumps(body)[:200]}")
                failures += 1
            else:
                content = (choices[0].get("message") or {}).get("content") or ""
                ok(f"completion: {content[:200]!r}")
        else:
            fail(f"non-JSON body: {str(body)[:200]}")
            failures += 1
    except urllib.error.HTTPError as exc:
        fail(f"HTTP {exc.code} -> {exc.read()[:200]!r}")
        failures += 1
    except Exception as exc:
        fail(f"request failed: {exc}")
        failures += 1

    print()
    if failures:
        print(f"RESULT: {failures} check(s) failed")
        return 1
    print("RESULT: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
