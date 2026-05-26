#!/usr/bin/env python3
"""Static dashboard frontend inventory.

This script reports review candidates only. It does not attempt to prove that a
CSS class or function is dead, because the dashboard uses generated HTML and
dynamic event wiring.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
INDEX_PATH = BASE_DIR / "web/operator_dashboard/index.html"


def selector_classes(style_text: str) -> set[str]:
    classes = set()
    for match in re.finditer(r"\.([A-Za-z_][A-Za-z0-9_-]*)", style_text):
        classes.add(match.group(1))
    return classes


def referenced_classes(html: str) -> set[str]:
    classes = set()
    for attr in re.finditer(r'class\s*=\s*["\']([^"\']+)["\']', html):
        classes.update(part for part in re.split(r"\s+", attr.group(1).strip()) if part)
    for match in re.finditer(r"classList\.(?:add|remove|toggle|contains)\(([^)]*)\)", html):
        classes.update(re.findall(r'["\']([A-Za-z_][A-Za-z0-9_-]*)["\']', match.group(1)))
    for match in re.finditer(r"(?:querySelector|querySelectorAll)\(([^)]*)\)", html):
        classes.update(re.findall(r"\.([A-Za-z_][A-Za-z0-9_-]*)", match.group(1)))
    return classes


def function_names(script_text: str) -> list[str]:
    return re.findall(r"\b(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\(", script_text)


def global_state_candidates(script_text: str) -> list[str]:
    candidates = []
    for match in re.finditer(r"^    (?:let|const)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=", script_text, re.MULTILINE):
        line = script_text[:match.start()].count("\n") + 1
        name = match.group(1)
        if name.isupper():
            continue
        candidates.append(f"{name} (line {line})")
    return candidates


def main() -> int:
    html = INDEX_PATH.read_text(encoding="utf-8")
    style_blocks = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html, flags=re.DOTALL | re.IGNORECASE))
    script_blocks = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.DOTALL | re.IGNORECASE))

    css_classes = selector_classes(style_blocks)
    used_classes = referenced_classes(html)
    maybe_unused = sorted(css_classes - used_classes)

    functions = function_names(script_blocks)
    duplicate_functions = sorted(name for name, count in Counter(functions).items() if count > 1)
    single_reference = []
    for name in sorted(set(functions)):
        if len(re.findall(rf"\b{re.escape(name)}\b", script_blocks)) <= 1:
            single_reference.append(name)

    console_count = len(re.findall(r"\bconsole\.log\s*\(", script_blocks))
    timer_counts = {
        "setTimeout": len(re.findall(r"\bsetTimeout\s*\(", script_blocks)),
        "clearTimeout": len(re.findall(r"\bclearTimeout\s*\(", script_blocks)),
        "setInterval": len(re.findall(r"\bsetInterval\s*\(", script_blocks)),
        "clearInterval": len(re.findall(r"\bclearInterval\s*\(", script_blocks)),
    }
    globals_for_review = global_state_candidates(script_blocks)

    print("Dashboard frontend audit")
    print("========================")
    print(f"CSS classes declared: {len(css_classes)}")
    print(f"CSS classes referenced directly: {len(used_classes)}")
    print(f"Possible unused CSS classes: {len(maybe_unused)}")
    for name in maybe_unused[:80]:
        print(f"  - {name}")
    if len(maybe_unused) > 80:
        print(f"  ... {len(maybe_unused) - 80} more")

    print(f"\nDuplicate JS function names: {len(duplicate_functions)}")
    if duplicate_functions:
        for name in duplicate_functions:
            print(f"  - {name}")
    else:
        print("  - none detected")

    print(f"\nconsole.log calls: {console_count}")

    print("\nPossible single-reference functions:")
    if single_reference:
        for name in single_reference[:80]:
            print(f"  - {name}")
        if len(single_reference) > 80:
            print(f"  ... {len(single_reference) - 80} more")
    else:
        print("  - none detected")

    print("\nTimer usage counts:")
    for name, count in timer_counts.items():
        print(f"  - {name}: {count}")

    print("\nGlobal state variables needing review:")
    if globals_for_review:
        for name in globals_for_review[:80]:
            print(f"  - {name}")
        if len(globals_for_review) > 80:
            print(f"  ... {len(globals_for_review) - 80} more")
    else:
        print("  - none detected")

    print("\nNo frontend files were modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
