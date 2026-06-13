#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


REPLACEMENTS = (
    (
        'f"Sorry, I encountered an error ({error_type}).\\n"',
        'f"Извини, произошла ошибка ({error_type}).\\n"',
        "gateway error heading",
    ),
    (
        '"Try again or use /reset to start a fresh session."',
        '"Попробуй ещё раз или используй /reset, чтобы начать новую сессию."',
        "gateway error hint",
    ),
)


def patch_base_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False

    for old, new, label in REPLACEMENTS:
        if new in text:
            continue
        if old not in text:
            raise SystemExit(f"Could not find patch target: {label}")
        text = text.replace(old, new, 1)
        changed = True

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch Hermes gateway fallback error text to Russian.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="/opt/hermes/gateway/platforms/base.py",
        help="Path to Hermes gateway/platforms/base.py.",
    )
    args = parser.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"Target file not found: {path}")

    changed = patch_base_file(path)
    print("patched" if changed else "already-patched")


if __name__ == "__main__":
    main()
