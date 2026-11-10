"""Fail if user-visible strings are hardcoded in web UI components."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src"

SCAN = [
    WEB / "App.tsx",
    *(WEB / "components").glob("*.tsx"),
]

SKIP_FILES = {"icons.tsx"}

# JSX text: > ... < with letters (not only {expr})
JSX_TEXT = re.compile(r">([^<{][^<()=]*[A-Za-z]{3}[^<()=]*)<")

# User-facing attributes
ATTR = re.compile(
    r"""(?:title|placeholder|aria-label|sub)\s*=\s*(?:\{?\s*["']([^"']*[A-Za-z]{2}[^"']*)["']\s*\}?|["']([^"']*[A-Za-z]{2}[^"']*)["'])"""
)

ALLOW_LINE = re.compile(
    r"t\s*\(|useT\s*\(|tx\s*\(|columnLabel\s*\(|stateLabel\s*\(|agoLocalized\s*\(|data-testid|className|role=|type=|accept=|spellCheck|"
    r"min=|max=|value=\{|aria-sort|aria-selected|aria-hidden|aria-modal|aria-live|aria-valuenow|effectAllowed|"
    r"console\.|eslint|http|magnet:|\.torrent|SOCKS|uTP|TCP|Ctrl\+|KB/s|∞|▲|▼|▸|▾|✓|Promise|usePoll|ReactNode"
)

ALLOW_LITERAL = re.compile(
    r"^(Space|Delete|Done|Mixed|Unknown|uTP|TCP|None|HTTP|SOCKS4|SOCKS5|GET|POST|#|\+|/|Del)$",
    re.I,
)


def check_file(path: Path) -> list[str]:
    if path.name in SKIP_FILES:
        return []
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(ROOT).as_posix()
    issues: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_LINE.search(line):
            continue
        for m in JSX_TEXT.finditer(line):
            s = m.group(1).strip()
            if s and not ALLOW_LITERAL.match(s) and not s.startswith("{") and "t(" not in s:
                issues.append(f"{rel}:{i}: JSX text {s!r}")
        for m in ATTR.finditer(line):
            s = (m.group(1) or m.group(2) or "").strip()
            if s and not ALLOW_LITERAL.match(s):
                issues.append(f"{rel}:{i}: attribute string {s!r}")
    return issues


def main() -> int:
    issues: list[str] = []
    for path in SCAN:
        if path.is_file():
            issues.extend(check_file(path))
    if issues:
        print("Hardcoded UI strings (use web/src/i18n t() keys):", file=sys.stderr)
        for x in issues[:40]:
            print(f"  {x}", file=sys.stderr)
        if len(issues) > 40:
            print(f"  ... and {len(issues) - 40} more", file=sys.stderr)
        return 1
    print("check_strings: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
