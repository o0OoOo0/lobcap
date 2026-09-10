"""Offline synthetic model validation; --hftbacktest requires isolated pinned deps."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lobcap.backtest.contract_validation import validate_contracts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hftbacktest", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate_contracts(external=args.hftbacktest)
    except Exception as exc:
        report = {"passed": False, "external": {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}}
    content = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
