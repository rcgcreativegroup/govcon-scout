#!/usr/bin/env python3
"""Compare operator dashboard backend routes with frontend route calls."""

import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_PATH = BASE_DIR / "src/operator_dashboard.py"
FRONTEND_PATH = BASE_DIR / "web/operator_dashboard/index.html"


DYNAMIC_PREFIXES = {
    "/api/action/",
    "/api/notes/",
    "/api/post-ai-status/",
    "/api/upload/",
    "/api/workspace-context/",
    "/api/workspace-draft/",
    "/api/workspace-history/",
}


def read_text(path):
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_template(path):
    path = path.split("${", 1)[0]
    path = path.split("{", 1)[0]
    return path


def extract_backend_routes(text):
    routes = set()
    patterns = [
        r"parsed\.path\s*==\s*['\"]([^'\"]+)['\"]",
        r"parsed\.path\.startswith\(\s*['\"]([^'\"]+)['\"]\s*\)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            route = match.group(1)
            if "/api/" in route or route == "/file":
                routes.add(route)
    return sorted(routes)


def extract_frontend_routes(text):
    routes = set()
    patterns = [
        r"fetch\(\s*([`'\"])(.*?)\1",
        r"api\(\s*([`'\"])(.*?)\1",
        r"return\s+([`'\"])(.*?)\1",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            route = match.group(2)
            if "/api/" in route or route.startswith("/file"):
                routes.add(route)
    return sorted(routes)


def route_matches(frontend_route, backend_route):
    frontend_base = normalize_template(frontend_route)
    if frontend_base == backend_route:
        return True
    if backend_route in DYNAMIC_PREFIXES and frontend_base.startswith(backend_route):
        return True
    if backend_route == "/file" and frontend_base.startswith("/file"):
        return True
    return False


def covered(route, backend_routes):
    return any(route_matches(route, backend_route) for backend_route in backend_routes)


def has_frontend_caller(backend_route, frontend_routes):
    return any(route_matches(frontend_route, backend_route) for frontend_route in frontend_routes)


def print_section(title, values):
    print(f"\n{title}")
    print("-" * len(title))
    if not values:
        print("None")
        return
    for value in values:
        print(f"- {value}")


def main():
    backend_routes = extract_backend_routes(read_text(BACKEND_PATH))
    frontend_routes = extract_frontend_routes(read_text(FRONTEND_PATH))

    missing_backend = [
        route for route in frontend_routes
        if not covered(route, backend_routes)
    ]
    unused_backend = [
        route for route in backend_routes
        if not has_frontend_caller(route, frontend_routes)
    ]

    print_section("Backend Routes Found", backend_routes)
    print_section("Frontend Routes Found", frontend_routes)
    print_section("Frontend Calls Without Backend Coverage", missing_backend)
    print_section("Backend Routes With No Frontend Caller", unused_backend)

    return 1 if missing_backend else 0


if __name__ == "__main__":
    raise SystemExit(main())
