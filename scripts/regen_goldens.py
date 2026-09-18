#!/usr/bin/env python3
"""Re-record the golden replays' checkpoints from the current engine.

Run the test suite **first**: an intentional engine change that alters state
makes the golden tests fail, and that failure is the signal to come here.
This tool then replays each log's existing `actions` through the engine and
rewrites its `checkpoints` to match. It never touches the `actions`, so it
cannot invent a new game — but it *will* absorb a regression if you run it
without reading the diff, so review the diff before committing.

Formatting is kept byte-stable: `json.dumps(log, indent=2)` with no trailing
newline, matching how the goldens are written, so the diff is exactly the
fields that changed.

Usage: python scripts/regen_goldens.py [--replays DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from struggler.engine.replay import run_with_checkpoints  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--replays", default="tests/replays")
    args = ap.parse_args()

    changed = 0
    for path in sorted(Path(args.replays).glob("*.json")):
        log = json.loads(path.read_text(encoding="utf-8"))
        if "checkpoints" not in log:
            print(f"skip (no checkpoints): {path.name}")
            continue
        before = json.dumps(log, indent=2)
        log["checkpoints"] = run_with_checkpoints(log)
        after = json.dumps(log, indent=2)
        if after == before:
            print(f"unchanged: {path.name} ({len(log['checkpoints'])} checkpoints)")
            continue
        path.write_text(after, encoding="utf-8")
        changed += 1
        print(f"regenerated: {path.name} ({len(log['checkpoints'])} checkpoints)")

    print(f"\n{changed} file(s) rewritten. Review the diff, then run: pytest -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
