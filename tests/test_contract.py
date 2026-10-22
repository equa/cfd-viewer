#!/usr/bin/env python3
"""Replay the recorded HTTP contract against the current code (design D11).

Run directly or under pytest:

    python tests/test_contract.py
    pytest tests/test_contract.py

Always fails on an exchange whose answer lost a recorded field, changed a
field's JSON type, or changed a status (outside ``contract.ALLOWED_CHANGES``).
Added fields pass: the API may only grow.

When the inputs match the recording's fingerprint, it also fails on any
changed value or a single changed byte of geometry or colour table. When they
do not, values are not compared -- data is not API -- and it says so; re-record
with ``tools/record_contract.py``.
"""

import json
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import contract  # noqa: E402


def replay():
    """``(total, failures, exact, recorded_fingerprint, now)``."""
    snapshot = json.loads(contract.SNAPSHOT.read_text())
    then, now = snapshot.get("fingerprint"), contract.fingerprint()
    exact = then == now
    failures = []
    with contract.server() as base:
        # The same warm-up the recorder did, so pipeline state matches.
        contract.record(base, f"/api/meta?case={contract.CASE}", "json")
        for recorded in snapshot["exchanges"]:
            actual = contract.record(base, recorded["path"], recorded["kind"])
            diffs = contract.compare(recorded, actual, exact)
            if diffs:
                failures.append((recorded["path"], diffs))
    return len(snapshot["exchanges"]), failures, exact, then, now


def _mode(exact, then, now):
    if exact:
        return "shape and values (inputs match the recording)"
    changed = [k for k in sorted(set(then or {}) | set(now)) if (then or {}).get(k) != now.get(k)]
    return ("shape only -- values not compared: inputs changed since recording ("
            + ", ".join(changed) + "); re-record with tools/record_contract.py")


def test_contract():
    total, failures, exact, then, now = replay()
    if not exact:
        warnings.warn(_mode(exact, then, now), stacklevel=1)
    assert not failures, "\n".join(
        f"{path}\n    " + "\n    ".join(diffs[:8]) for path, diffs in failures)


def main():
    total, failures, exact, then, now = replay()
    print(f"compared: {_mode(exact, then, now)}")
    for path, diffs in failures:
        print(f"  FAIL {path}")
        for d in diffs[:8]:
            print(f"       {d}")
    print(f"\n{total - len(failures)}/{total} exchanges match the recorded contract")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
