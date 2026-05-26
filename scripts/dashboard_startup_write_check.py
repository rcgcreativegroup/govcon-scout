#!/usr/bin/env python3
"""Static guard for read-only operator dashboard startup."""

import ast
import re
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_PATH = BASE_DIR / "src/operator_dashboard.py"

WRITE_PATTERNS = [
    "to_csv",
    "open(..., \"w\")",
    "open(..., \"a\")",
    "shutil.copy",
    "backup",
    "write_text",
    "write_csv_preserve",
]

STARTUP_FUNCTIONS = {"initialize"}


def read_source():
    return DASHBOARD_PATH.read_text(encoding="utf-8", errors="replace")


def line_has_write_pattern(line):
    checks = [
        "to_csv" in line,
        bool(re.search(r"\.open\([^)]*['\"]w", line)),
        bool(re.search(r"\.open\([^)]*['\"]a", line)),
        bool(re.search(r"\bopen\([^)]*['\"]w", line)),
        bool(re.search(r"\bopen\([^)]*['\"]a", line)),
        "shutil.copy" in line,
        "backup" in line,
        "write_text" in line,
        "write_csv_preserve" in line,
    ]
    return any(checks)


def write_pattern_lines(source):
    ignored = ignored_line_numbers(source)
    findings = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        if lineno in ignored:
            continue
        if line_has_write_pattern(line):
            findings.append((lineno, line.strip()))
    return findings


def ignored_line_numbers(source):
    ignored = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant):
            if isinstance(first.value.value, str):
                start = first.lineno
                end = getattr(first, "end_lineno", first.lineno)
                ignored.update(range(start, end + 1))
    for lineno, line in enumerate(source.splitlines(), start=1):
        if line.strip().startswith("#"):
            ignored.add(lineno)
    return ignored


def function_ranges(source):
    tree = ast.parse(source)
    ranges = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ranges[node.name] = (node.lineno, getattr(node, "end_lineno", node.lineno))
    return ranges


def in_range(lineno, bounds):
    start, end = bounds
    return start <= lineno <= end


def main():
    source = read_source()
    findings = write_pattern_lines(source)
    ranges = function_ranges(source)

    print("Dashboard startup write check")
    print("=============================")
    print(f"Scanned: {DASHBOARD_PATH.relative_to(BASE_DIR)}")
    print("")
    print("Write-like patterns searched:")
    for pattern in WRITE_PATTERNS:
        print(f"- {pattern}")

    print("")
    print("Found write-like patterns:")
    if not findings:
        print("- None")
    else:
        for lineno, line in findings:
            print(f"- line {lineno}: {line}")

    startup_issues = []
    for name in STARTUP_FUNCTIONS:
        bounds = ranges.get(name)
        if not bounds:
            startup_issues.append((name, "function not found"))
            continue
        for lineno, line in findings:
            if in_range(lineno, bounds):
                startup_issues.append((name, f"line {lineno}: {line}"))

    print("")
    if startup_issues:
        print("Confirmed startup mutation risk:")
        for name, detail in startup_issues:
            print(f"- {name}: {detail}")
    else:
        print("Confirmed startup mutation risk: none found in known startup functions.")

    print("")
    print("Manual review required for write helpers; startup should remain read-only.")
    return 1 if startup_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
