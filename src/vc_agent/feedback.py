#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys

from .preferences import append_feedback


def main() -> None:
    p = argparse.ArgumentParser(description="VC Agent 简报条目反馈")
    p.add_argument("--item", required=True, help="条目原文链接")
    p.add_argument("--vote", required=True, choices=("up", "down"), help="up=有用, down=不想看")
    p.add_argument("--source", default=None, help="可选来源")
    p.add_argument("--author", default=None, help="可选作者")
    args = p.parse_args()
    try:
        append_feedback(args.item, args.vote, source=args.source, author=args.author)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    print("[OK] 已记录反馈并重算 preferences.json")


if __name__ == "__main__":
    main()
