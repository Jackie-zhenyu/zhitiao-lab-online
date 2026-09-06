"""Read-only delivery checks. Never inspect configuration values or API keys."""

from __future__ import annotations

import argparse
import importlib
from importlib import metadata
from pathlib import Path
import sys


def check_runtime(expected_venv: Path) -> list[str]:
    errors = []
    if sys.version_info[:2] != (3, 12):
        errors.append("The locked delivery environment requires Python 3.12.")
    if sys.prefix == sys.base_prefix:
        errors.append("Use the project virtual environment; system Python is not an install target.")
    if Path(sys.prefix).resolve() != expected_venv.resolve():
        errors.append("The interpreter does not belong to the expected project virtual environment.")
    if expected_venv.is_symlink() or expected_venv.resolve().parent != expected_venv.parent.resolve():
        errors.append("The project .venv must not redirect to another directory.")
    config = expected_venv / "pyvenv.cfg"
    if not config.is_file():
        errors.append("The virtual environment is incomplete (pyvenv.cfg is missing).")
    elif "include-system-site-packages = false" not in config.read_text(encoding="utf-8").lower():
        errors.append("The virtual environment must isolate system packages.")
    return errors


def check_dependencies(lockfile: Path) -> list[str]:
    errors = []
    for line in lockfile.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        name, expected = line.strip().split("==", 1)
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            errors.append(f"Missing dependency: {name}=={expected}.")
            continue
        if actual != expected:
            errors.append(f"Dependency version mismatch: {name}; expected {expected}, found {actual}.")
    if not errors:
        for name in ("streamlit", "pandas", "numpy", "scipy", "plotly", "pydantic", "openai", "httpx", "dotenv", "pytest"):
            try:
                importlib.import_module(name)
            except Exception:
                errors.append(f"Cannot import {name}; repair the project environment with scripts/install.cmd.")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, metavar="LOCKFILE")
    args = parser.parse_args()
    errors = check_runtime(args.venv)
    if not errors and args.dependencies:
        errors.extend(check_dependencies(args.dependencies))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"Python {sys.version.split()[0]} ({sys.platform}); project virtual environment verified.")
    if args.dependencies:
        print("All locked dependency versions and application imports verified. No AI request was made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
