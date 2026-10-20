#!/usr/bin/env python3
"""Replay the recorded HTTP contract against the current code (design D11).

Run directly or under pytest:

    python tests/test_contract.py
    pytest tests/test_contract.py

Fails on any exchange whose answer lost or changed a recorded field, changed a
status (outside ``contract.ALLOWED_CHANGES``), or changed a single byte of
scene geometry or a colour table. Added fields pass: the API may only grow.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contract  # noqa: E402


def replay():
    """Per-exchange differences, as ``[(path, [diff, ...]), ...]``."""
    snapshot = json.loads(contract.SNAPSHOT.read_text())
    failures = []
    with contract.server() as base:
        # The same warm-up the recorder did, so pipeline state matches.
        contract.record(base, f"/api/meta?case={contract.CASE}", "json")
        for recorded in snapshot["exchanges"]:
            actual = contract.record(base, recorded["path"], recorded["kind"])
            diffs = contract.compare(recorded, actual)
            if diffs:
                failures.append((recorded["path"], diffs))
    return len(snapshot["exchanges"]), failures


def test_contract():
    total, failures = replay()
    assert not failures, "\n".join(
        f"{path}\n    " + "\n    ".join(diffs[:8]) for path, diffs in failures)


def main():
    total, failures = replay()
    for path, diffs in failures:
        print(f"  FAIL {path}")
        for d in diffs[:8]:
            print(f"       {d}")
    print(f"\n{total - len(failures)}/{total} exchanges match the recorded contract")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
