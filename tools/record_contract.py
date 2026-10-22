#!/usr/bin/env python3
"""Record the HTTP contract snapshot (design D11, layer 1).

    python tools/record_contract.py            # writes tests/contract/hotroom.json
    python tools/record_contract.py --out X    # somewhere else, e.g. to diff two runs

Re-record when the *inputs* change -- the demo case is regenerated or
trimmed, VTK is upgraded -- and the replay reports "values not compared".
That re-records values on known-good code. The *shape* is the contract
proper: a recording that drops or retypes a field the old one had is an API
change, so review the snapshot's diff before committing it.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

import contract  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=contract.SNAPSHOT)
    args = ap.parse_args()

    with contract.server() as base:
        meta = contract.record(base, f"/api/meta?case={contract.CASE}", "json")["json"]
        exchanges = [contract.record(base, path, kind)
                     for path, kind in contract.queries(meta)]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"case": contract.CASE,
                                    "fingerprint": contract.fingerprint(),
                                    "exchanges": exchanges},
                                   indent=1, sort_keys=True) + "\n")
    print(f"{len(exchanges)} exchanges -> {args.out}")


if __name__ == "__main__":
    main()
