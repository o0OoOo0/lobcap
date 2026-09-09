"""Workspace CLI uses exactly the UI resolver and durable submission path."""
import argparse
import json
from pathlib import Path

from lobcap.config.backtest import load_backtest_config
from .contracts import StrategyDraft
from .resolve import resolve_experiment
from .store import WorkspaceStore, read_json
from .jobs import launch_worker, recover_interrupted


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preview", "submit", "status", "cancel", "resume"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--config")
    parser.add_argument("--sample")
    parser.add_argument("--design", help="JSON with name, hypothesis, axes, scenarios, research_policy")
    parser.add_argument("--job")
    parser.add_argument("--submission-key")
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args(argv)
    store = WorkspaceStore(args.root)
    if args.action in {"preview", "submit"}:
        if not args.config:
            parser.error("--config is required")
        design = read_json(args.design) if args.design else {}
        draft = StrategyDraft(load_backtest_config(args.config), design.get("name", "CLI experiment"),
            design.get("hypothesis", ""), research_policy=design.get("research_policy"))
        spec = resolve_experiment(draft, design.get("axes"), design.get("scenarios"), args.sample)
        if args.action == "preview":
            print(json.dumps(spec, ensure_ascii=False, indent=2))
        else:
            if not args.submission_key:
                parser.error("--submission-key is required; reuse it only for an identical submission")
            job_id = store.submit(spec, args.submission_key)
            if not args.no_launch:
                launch_worker(store.root)
            print(job_id)
    elif args.action == "status":
        recover_interrupted(store.root)
        print(json.dumps(store.get_job(args.job) if args.job else store.list_jobs(), ensure_ascii=False, indent=2))
    else:
        if not args.job:
            parser.error("--job is required")
        result = store.request_cancel(args.job) if args.action == "cancel" else store.resume(args.job)
        if args.action == "resume" and not args.no_launch:
            launch_worker(store.root)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
