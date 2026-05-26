#!/usr/bin/env python3
"""Lightweight requirements/import inventory for GovCon Scout.

This script is intentionally conservative: it reports likely mismatches but
does not edit requirements.txt. Optional/import-lazy packages should be reviewed
before removal or addition.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS_PATH = BASE_DIR / "requirements.txt"
SCAN_DIRS = [BASE_DIR / "src", BASE_DIR / "scripts"]

PACKAGE_TO_IMPORT = {
    "python-dotenv": "dotenv",
    "beautifulsoup4": "bs4",
    "pypdf": "pypdf",
}

IMPORT_TO_PACKAGE = {value: key for key, value in PACKAGE_TO_IMPORT.items()}

LIKELY_OPTIONAL = {
    "anthropic": "AI Workspace chat is optional and import-lazy.",
    "pandas": "May be used by analysis notebooks or optional workflows; verify before removal.",
    "playwright": "Browser/SAM.gov workflows may import this lazily.",
    "pypdf": "Document parsing workflows may import this lazily.",
    "openpyxl": "Excel parsing is commonly used indirectly by document workflows.",
    "pytz": "Dashboard has a fallback if pytz is unavailable.",
}


def read_requirements() -> list[str]:
    packages: list[str] = []
    if not REQUIREMENTS_PATH.exists():
        return packages
    for raw_line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("==", 1)[0].split(">=", 1)[0].split("<=", 1)[0].strip()
        if name:
            packages.append(name)
    return packages


def local_module_names() -> set[str]:
    names = set()
    for directory in SCAN_DIRS:
        if not directory.exists():
            continue
        names.update(path.stem for path in directory.glob("*.py"))
    return names


def imported_modules() -> tuple[set[str], dict[str, set[str]]]:
    imports: set[str] = set()
    by_file: dict[str, set[str]] = {}
    for directory in SCAN_DIRS:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.py")):
            rel = str(path.relative_to(BASE_DIR))
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as error:
                by_file.setdefault(rel, set()).add(f"SYNTAX_ERROR:{error.lineno}")
                continue
            file_imports: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        file_imports.add(alias.name.split(".", 1)[0])
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.level == 0:
                        file_imports.add(node.module.split(".", 1)[0])
            imports.update(file_imports)
            by_file[rel] = file_imports
    return imports, by_file


def is_stdlib(name: str) -> bool:
    if name in {"__future__"}:
        return True
    stdlib = getattr(sys, "stdlib_module_names", set())
    return name in stdlib


def main() -> int:
    requirements = read_requirements()
    req_imports = {PACKAGE_TO_IMPORT.get(package, package).lower(): package for package in requirements}
    imports, by_file = imported_modules()
    local_modules = local_module_names()

    third_party = sorted(
        name for name in imports
        if name not in local_modules and not is_stdlib(name)
    )
    missing = [
        IMPORT_TO_PACKAGE.get(name, name)
        for name in third_party
        if name.lower() not in req_imports
    ]
    unused = [
        package for package in requirements
        if PACKAGE_TO_IMPORT.get(package, package).lower() not in {name.lower() for name in imports}
    ]

    print("Dependency audit")
    print("================")
    print("\nPackages in requirements.txt:")
    for package in requirements:
        print(f"  - {package}")

    print("\nTop-level imports found:")
    for name in sorted(imports):
        print(f"  - {name}")

    print("\nThird-party imports missing from requirements.txt:")
    if missing:
        for name in sorted(set(missing)):
            note = LIKELY_OPTIONAL.get(name, "")
            suffix = f" ({note})" if note else ""
            print(f"  - {name}{suffix}")
    else:
        print("  - none detected")

    print("\nRequirements not obviously imported by src/*.py or scripts/*.py:")
    if unused:
        for package in unused:
            note = LIKELY_OPTIONAL.get(package, "Review before removal.")
            print(f"  - {package}: {note}")
    else:
        print("  - none detected")

    print("\nLikely optional packages:")
    for package in requirements:
        if package in LIKELY_OPTIONAL:
            print(f"  - {package}: {LIKELY_OPTIONAL[package]}")

    syntax_errors = {
        rel: sorted(values)
        for rel, values in by_file.items()
        if any(value.startswith("SYNTAX_ERROR:") for value in values)
    }
    if syntax_errors:
        print("\nFiles needing parser review:")
        for rel, values in syntax_errors.items():
            print(f"  - {rel}: {', '.join(values)}")
        return 1

    print("\nNo dependency changes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
