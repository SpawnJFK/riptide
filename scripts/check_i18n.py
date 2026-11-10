"""Warn when sr.json drifts from en.json; never fails verify (English-only product)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "web" / "src" / "i18n"


def keys(obj: object, prefix: str = "") -> set[str]:
    if isinstance(obj, dict):
        out: set[str] = set()
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out |= keys(v, p)
        return out
    return {prefix}


def main() -> int:
    en = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    sr = json.loads((I18N / "sr.json").read_text(encoding="utf-8"))
    ke, ks = keys(en), keys(sr)
    missing_sr = sorted(ke - ks)
    missing_en = sorted(ks - ke)
    if missing_sr or missing_en:
        if missing_sr:
            print(f"check_i18n: warning: {len(missing_sr)} key(s) missing in sr.json (frozen locale)", file=sys.stderr)
        if missing_en:
            print(f"check_i18n: warning: {len(missing_en)} extra key(s) in sr.json", file=sys.stderr)
    print("check_i18n: ok (sr drift warnings only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
