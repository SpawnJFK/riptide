"""One command that proves the repo is healthy. Run it before every commit; CI runs the same.

    python scripts/verify.py            # everything
    python scripts/verify.py --fast     # skip the web build

Prints one newline-terminated status line per step.
Exit code is non-zero if any step fails. Output ends with a VERIFIED/BROKEN summary.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIN = sys.platform == "win32"
PY = sys.executable


def run(name: str, cmd: list[str], cwd: Path) -> tuple[str, bool, str]:
    t0 = time.monotonic()
    print(f"[verify] {name} ... running", flush=True)
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=WIN and cmd[0] in ("npm", "npx"))
    dt = time.monotonic() - t0
    ok = p.returncode == 0
    tail = (p.stdout + p.stderr).strip().splitlines()[-12:]
    print(f"[verify] {name} ... {'ok' if ok else 'FAILED'} ({dt:.1f}s)", flush=True)
    if not ok:
        for line in tail:
            print(f"    {line}")
    return name, ok, "\n".join(tail)


def contract() -> tuple[str, bool, str]:
    doc = (ROOT / "SERVICE_CONTRACTS.md").read_text("utf-8")
    m_doc = re.search(r"Contract version:\s*([\d.]+)", doc)
    m_py = re.search(r'CONTRACT_VERSION = "([\d.]+)"', (ROOT / "backend/riptide/__init__.py").read_text("utf-8"))
    m_ts = re.search(r'CONTRACT_VERSION = "([\d.]+)"', (ROOT / "web/src/contract.ts").read_text("utf-8"))
    versions = {k: (m.group(1) if m else None) for k, m in (("doc", m_doc), ("backend", m_py), ("web", m_ts))}
    digest = hashlib.sha256(doc.encode()).hexdigest()[:12]
    ok = len(set(versions.values())) == 1 and None not in versions.values()
    print(f"[verify] contract ... {'ok' if ok else 'FAILED'} versions={versions} sync_hash={digest}", flush=True)
    return "contract", ok, digest


def main() -> int:
    fast = "--fast" in sys.argv
    results = []
    be, web = ROOT / "backend", ROOT / "web"
    results.append(run("ruff", [PY, "-m", "ruff", "check", "riptide", "tests"], be))
    results.append(run("ruff format", [PY, "-m", "ruff", "format", "--check", "riptide", "tests"], be))
    results.append(run("pytest", [PY, "-m", "pytest", "-q", "-p", "no:warnings"], be))
    if shutil.which("npm") and (web / "node_modules").exists():
        results.append(run("typecheck", ["npx", "tsc", "-b"], web))
        if not fast:
            results.append(run("web build", ["npx", "vite", "build"], web))
    else:
        print("[verify] web ... SKIPPED (run npm install in web/)", flush=True)
        results.append(("web", False, "node_modules missing"))
    results.append(run("license gate", [PY, str(ROOT / "scripts/check_licenses.py")], ROOT))
    results.append(run("check_strings", [PY, str(ROOT / "scripts/check_strings.py")], ROOT))
    results.append(run("check_i18n", [PY, str(ROOT / "scripts/check_i18n.py")], ROOT))
    results.append(contract())
    failed = [r[0] for r in results if not r[1]]
    print("[verify] " + ("VERIFIED all steps passed" if not failed else f"BROKEN: {', '.join(failed)}"), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
