#!/usr/bin/env python3
"""Lightweight route smoke tests for the local operator dashboard."""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "http://127.0.0.1:8765"


def request(base_url, method, path, payload=None):
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read()
            return response.status, body, response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers.get("Content-Type", "")
    except OSError as error:
        return None, str(error).encode("utf-8"), ""


def parse_json(body):
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def pass_fail(label, ok, detail):
    status = "PASS" if ok else "FAIL"
    print(f"{status} {label} - {detail}")
    return ok


def run_checks(base_url):
    checks = []

    status, _body, _content_type = request(base_url, "GET", "/")
    checks.append(pass_fail("GET /", status == 200, f"status={status}"))

    status, body, content_type = request(base_url, "GET", "/api/workspace-sessions")
    parsed = parse_json(body)
    workspace_ok = status == 200 and isinstance(parsed, dict)
    detail = f"status={status}, json={'yes' if isinstance(parsed, dict) else 'no'}"
    if content_type:
        detail += f", content_type={content_type}"
    checks.append(pass_fail("GET /api/workspace-sessions", workspace_ok, detail))

    status, body, _content_type = request(base_url, "GET", "/file?path=reports")
    parsed = parse_json(body)
    directory_ok = status == 403 and isinstance(parsed, dict)
    checks.append(pass_fail("GET /file?path=reports", directory_ok, f"status={status}"))

    status, body, _content_type = request(
        base_url,
        "POST",
        "/api/auto-archive-pastdue",
        {"preview": True},
    )
    parsed = parse_json(body)
    preview_ok = (
        status == 200
        and isinstance(parsed, dict)
        and parsed.get("status") == "ok"
        and parsed.get("preview") is True
    )
    checks.append(pass_fail("POST /api/auto-archive-pastdue preview", preview_ok, f"status={status}"))

    return all(checks)


def parse_args():
    parser = argparse.ArgumentParser(description="Smoke test local GovCon Scout dashboard routes.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    return parser.parse_args()


def main():
    args = parse_args()
    ok = run_checks(args.base_url)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
