"""Project-local install/start commands using only the Python standard library."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], root: Path) -> int:
    """Do not echo environment variables, model configuration, or shell commands."""
    try:
        return subprocess.run(command, cwd=root, check=False).returncode
    except OSError:
        print("ERROR: Cannot start the required program; check the interpreter path and permissions.", file=sys.stderr)
        return 1


def venv_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def environment_check(root: Path, *, dependencies: bool) -> int:
    python = venv_python(root)
    if not python.is_file():
        print("ERROR: Project .venv is missing or incomplete. Existing files were not removed; run scripts/install.cmd.", file=sys.stderr)
        return 1
    command = [str(python), str(root / "scripts/check_environment.py"), "--venv", str(root / ".venv")]
    if dependencies:
        command += ["--dependencies", str(root / "requirements.lock.txt")]
    return run(command, root)


def install(root: Path) -> int:
    if sys.version_info[:2] != (3, 12):
        print("ERROR: The verified lockfile requires Python 3.12. Supply its executable path to install.cmd.", file=sys.stderr)
        return 1
    lockfile = root / "requirements.lock.txt"
    if not lockfile.is_file():
        print("ERROR: requirements.lock.txt is missing. Use an intact project copy.", file=sys.stderr)
        return 1
    venv = root / ".venv"
    if venv.is_symlink() or venv.is_junction():
        print("ERROR: Project .venv redirects elsewhere. No files were changed; use an independent local environment.", file=sys.stderr)
        return 1
    if not venv.exists():
        print("Creating project .venv without system packages.", flush=True)
        if run([sys.executable, "-m", "venv", str(venv)], root):
            print("ERROR: Virtual environment creation failed. Check venv support and directory permissions. No files were removed.", file=sys.stderr)
            return 1
    else:
        print("Checking existing project .venv; its directory will not be overwritten.", flush=True)
    if environment_check(root, dependencies=False):
        return 1
    python = str(venv_python(root))
    print("Installing exact locked versions. Network access may be required; no API key is needed.", flush=True)
    command = [python, "-m", "pip", "--disable-pip-version-check", "--require-virtualenv", "install",
               "--only-binary=:all:", "--retries", "1", "--timeout", "30", "-r", str(lockfile)]
    if run(command, root):
        print("ERROR: Dependency installation failed. Review the pip error and check network/index access, Python version, or permissions. Installation is NOT complete.", file=sys.stderr)
        return 1
    if run([python, "-m", "pip", "--disable-pip-version-check", "check"], root):
        print("ERROR: pip check failed. Dependency installation has not been accepted.", file=sys.stderr)
        return 1
    if environment_check(root, dependencies=True):
        return 1
    print("Installation verified. Windows: scripts\\start.cmd; Linux/macOS: .venv/bin/python scripts/manage.py start.")
    return 0


def start(root: Path, *, port: int, check_only: bool) -> int:
    if not 1 <= port <= 65535:
        print("ERROR: Port must be between 1 and 65535.", file=sys.stderr)
        return 1
    if environment_check(root, dependencies=True):
        return 1
    if not (root / "app.py").is_file():
        print("ERROR: app.py is missing. Use an intact project copy.", file=sys.stderr)
        return 1
    if check_only:
        print("Startup prerequisites passed; no server or model request was started.")
        return 0
    print(f"Starting local workbench: http://127.0.0.1:{port} (Ctrl+C to stop).", flush=True)
    code = run([str(venv_python(root)), "-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1",
                "--server.port", str(port), "--server.headless", "true", "--browser.gatherUsageStats", "false"], root)
    if code:
        print("ERROR: Streamlit stopped with an error. If the port is occupied, reuse your running instance or choose --port 8502.", file=sys.stderr)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="Create/check .venv and install the full lockfile.")
    launcher = sub.add_parser("start", help="Check dependencies and start on localhost only.")
    launcher.add_argument("--port", type=int, default=8501)
    launcher.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        return install(ROOT) if args.command == "install" else start(ROOT, port=args.port, check_only=args.check_only)
    except KeyboardInterrupt:
        print("\nStopped. Session data is not persisted; downloaded reports remain on your computer.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
