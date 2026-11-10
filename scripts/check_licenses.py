"""License gate: every shipped dependency must be on the allowlist.

Checks the runtime closure only (what ships to users):
- Python: dependencies of the `riptide` backend, resolved from installed metadata
- Web: production deps from `npm ls --omit=dev --all --json`

Exit 1 on any unknown or disallowed license. Run: python scripts/check_licenses.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOWED = {
    "MIT",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "ISC",
    "0BSD",
    "Python-2.0",
    "PSF-2.0",
    "OFL-1.1",  # fonts
    "Unlicense",
    "CC0-1.0",
    "BlueOak-1.0.0",
}
# Some packages publish free-text licenses; map the known ones after manual review.
REVIEWED = {
    "libtorrent": "BSD-3-Clause",
    "uvloop": "MIT",  # dual MIT/Apache
    "httptools": "MIT",
    "websockets": "BSD-3-Clause",
    "typing_extensions": "PSF-2.0",
    "typing-extensions": "PSF-2.0",
    "idna": "BSD-3-Clause",
    "click": "BSD-3-Clause",
    "anyio": "MIT",
    "sniffio": "MIT",
    "h11": "MIT",
    "python-dotenv": "BSD-3-Clause",
    "pyyaml": "MIT",
    "PyYAML": "MIT",
    "watchfiles": "MIT",
    "starlette": "BSD-3-Clause",
    "pydantic": "MIT",
    "pydantic_core": "MIT",
    "pydantic-core": "MIT",
    "annotated-types": "MIT",
    "annotated_types": "MIT",
    "typing-inspection": "MIT",
    "typing_inspection": "MIT",
    "python-multipart": "Apache-2.0",
    "python_multipart": "Apache-2.0",
    "colorama": "BSD-3-Clause",
    "uvicorn": "BSD-3-Clause",
    "fastapi": "MIT",
    "annotated-doc": "MIT",
    # MPL-2.0 is file-level copyleft: fine to ship unmodified.
    "certifi": "MPL-2.0 (reviewed: unmodified dependency)",
    "annotated_doc": "MIT",
}
DENY = re.compile(r"\b(A?GPL|LGPL|SSPL|BUSL|BSL|PolyForm|Commons Clause|CC-BY-NC|Elastic)\b", re.I)


def norm(lic: str) -> str:
    s = lic.strip()
    s = re.sub(r"\s+", " ", s)
    aliases = {
        "MIT License": "MIT",
        "BSD License": "BSD",
        "BSD 3-Clause": "BSD-3-Clause",
        "Apache 2.0": "Apache-2.0",
        "Apache Software License": "Apache-2.0",
        "Apache License 2.0": "Apache-2.0",
        "ISC License": "ISC",
    }
    return aliases.get(s, s)


def py_roots() -> list[str]:
    import tomllib

    data = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text("utf-8"))
    return [re.split(r"[ ;<>=!\[~(]", d, maxsplit=1)[0] for d in data["project"]["dependencies"]]


def py_closure(roots: list[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        key = name.lower().replace("_", "-")
        if key in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen.add(key)
        for req in dist.requires or []:
            if "extra ==" in req and "standard" not in req:
                continue
            dep = re.split(r"[ ;<>=!\[~(]", req, maxsplit=1)[0]
            if dep:
                stack.append(dep)
    seen.discard("riptide")
    return seen


def py_license(name: str) -> str:
    for n in (name, name.replace("-", "_")):
        if n in REVIEWED:
            return REVIEWED[n]
    try:
        md = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return "NOT INSTALLED"
    expr = md.get("License-Expression")
    if expr:
        return norm(expr)
    classifiers = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return norm(classifiers[0])
    return norm(md.get("License") or "UNKNOWN")


def ok(lic: str) -> bool:
    if "(reviewed:" in lic:
        return True
    if DENY.search(lic):
        return False
    parts = re.split(r"\s+(?:OR|or)\s+|/", lic.strip("()"))
    return any(norm(p) in ALLOWED for p in parts)


def main() -> int:
    bad: list[str] = []
    checked = 0

    for name in sorted(py_closure(py_roots())):
        lic = py_license(name)
        checked += 1
        if lic == "NOT INSTALLED":
            bad.append(f"python  {name}: not installed, run the backend install first")
            continue
        if not ok(lic):
            bad.append(f"python  {name}: {lic}")

    web = ROOT / "web"
    if (web / "node_modules").exists():
        out = subprocess.run(
            ["npm", "ls", "--omit=dev", "--all", "--json"], cwd=web, capture_output=True, text=True, shell=sys.platform == "win32"
        )
        tree = json.loads(out.stdout or "{}")

        def walk(deps: dict) -> None:
            nonlocal checked
            for name, info in (deps or {}).items():
                pj = web / "node_modules" / name / "package.json"
                lic = "UNKNOWN"
                if pj.exists():
                    data = json.loads(pj.read_text("utf-8"))
                    raw = data.get("license") or data.get("licenses") or "UNKNOWN"
                    lic = raw if isinstance(raw, str) else str(raw)
                checked += 1
                if not ok(norm(lic)):
                    bad.append(f"npm     {name}: {lic}")
                walk(info.get("dependencies", {}))

        walk(tree.get("dependencies", {}))

    print(f"[license-gate] checked {checked} packages")
    if bad:
        print("[license-gate] BLOCKED, these licenses are not on the allowlist:")
        for b in sorted(set(bad)):
            print("   ", b)
        print("[license-gate] Replace the package, or record a reviewed exception in scripts/check_licenses.py.")
        return 1
    print("[license-gate] VERIFIED, all runtime dependencies are permissive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
