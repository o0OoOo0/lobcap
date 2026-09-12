#!/usr/bin/env python3
"""Run sealed DEVELOPMENT MM-0/MM-1 pairs using the existing simulator."""
import argparse
import json
from pathlib import Path

from run_signal_development import _code_fingerprint, _isolated
from validate_signal_development import validate as validate_p0
from lobcap.backtest.replay import write_json
from lobcap.backtest.research_flow.signal_study import development_windows, digest
from lobcap.backtest.research_flow.study1 import case_configs, sealed_config, run_window, summarize
from lobcap.backtest.runner import config_document
from lobcap.config.backtest import load_backtest_config
from lobcap.config.common import sha256_file
from lobcap.config.experiment import ROOT


def run(config_path, catalogue_path, p0_report, output, stage="baselines"):
    config = load_backtest_config(config_path)
    if config.clock_mode != "recorded":
        raise ValueError("Study 1 DEVELOPMENT requires recorded clock")
    case_configs(config, stage)
    stage_args = (stage,) if stage != "baselines" else ()
    code = _code_fingerprint(ROOT)
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    selected = development_windows(catalogue)
    prerequisite = validate_p0(p0_report, catalogue_path)
    p0 = json.loads(Path(p0_report).read_text(encoding="utf-8"))
    study = Path(output).resolve()
    # Preflight every selected sample before producing any market outcomes.
    for window, seal in selected:
        sealed_config(config, window, seal, config.output_path/"samples"/window.window_id,
                      study/window.window_id, *stage_args)
    protocol = {"version": f"study1-{stage}-v1", "code_fingerprint": code,
        "catalogue_sha256": catalogue["catalogue_sha256"],
        "p0_report_sha256": sha256_file(p0_report), "p0_code_fingerprint": p0["code_fingerprint"],
        "p0_acceptance": {"status": prerequisite["status"], "cache_checksums_verified": prerequisite["cache_checksums_verified"]},
        "base_config": config_document(config),
        "cases": {k: c.resolved() for k, c in case_configs(config, stage).items()},
        "window_ids": [w.window_id for w, _ in selected],
        "roles_opened": ["DEVELOPMENT"], "locked_roles_opened": [],
        "question": "What changes when inventory price skew is added to fixed-spread MM?",
        "primary_descriptive_endpoint": "paired MM-1 minus MM-0 net_pnl per reset window",
        "selection_rule": "all sealed PASS DEVELOPMENT windows; no outcome-based replacement",
        "initialization": "cash and inventory reset independently for every case/window",
        "cost_profile": "zero_cost_diagnostic" if config.maker_bps == config.taker_bps == 0 else "unverified_assumed_fees",
        "fees_verified": False, "promoted_signals": [], "promotion_decision": "DEFERRED",
        "statistical_significance": "NOT_CLAIMED", "paper_eligibility": "INSUFFICIENT_EVIDENCE"}
    if stage == "bp":
        protocol.update(question="What changes when causal Book Pressure is added to inventory-skew MM?",
            primary_descriptive_endpoint="paired MM-2 minus MM-1 net_pnl per reset window",
            experimental_signals=["imbalance_1"], promotion_decision="RESEARCH_ONLY",
            controls=["zero BP equals MM-1", "sign-reversed BP"],
            feature_acceptance="decision-time causal replay, 28-digit Decimal agreement, exact delivery provenance, stale/coverage gating")
    if stage == 'vpin':
        from lobcap.backtest.vpin import VERSION
        protocol.update(question='What changes when completed-volume VPIN widens inventory-skew quotes?',
            primary_descriptive_endpoint='paired MM-3 minus MM-1 net_pnl per reset window',
            experimental_signals=['vpin_normal_volume'],promotion_decision='RESEARCH_ONLY',
            controls=['zero width equals MM-1','constant 0.5 width with identical VPIN availability gate'],
            feature_version=VERSION,feature_acceptance='100ms causal prerequisite before execution, exact runtime state parity',
            toxicity_label='absolute CLIENT_FORWARD_RETURN ticks with accepted P0 freshness',
            toxicity_bucket_edges=['0.2','0.4','0.6','0.8'])
    protocol["protocol_sha256"] = digest(protocol)
    study.mkdir(parents=True, exist_ok=False)
    write_json(study/"protocol.json", protocol)
    rows, performance = [], []
    try:
        if stage == 'vpin':
            from lobcap.backtest.research_flow.study1_vpin import prerequisite_phase, summarize_prerequisite
            feature_rows=[]; feature_performance=[]
            for window,seal in selected:
                result=_isolated(prerequisite_phase,config,window,seal,p0['freshness_policy'],study)
                feature_rows.append(result['window'])
                feature_performance.append({'window_id':window.window_id,'wall_seconds':result['wall_seconds'],
                    'peak_rss_mib':result['peak_rss_mib']})
            prerequisite_report={'status':'PASS','protocol_sha256':protocol['protocol_sha256'],
                'code_fingerprint':code,'per_window':feature_rows,'performance':feature_performance,
                'scope':'input correctness and descriptive toxicity proxy; no promotion or predictive success requirement'}
            prerequisite_report['summary']=summarize_prerequisite(feature_rows)
            write_json(study/'vpin_prerequisite.json',prerequisite_report)
        for window, seal in selected:
            result = _isolated(run_window, config, window, seal,
                config.output_path/"samples"/window.window_id, study/window.window_id, *stage_args)
            rows.append(result["window"])
            performance.append({"window_id": window.window_id,
                "wall_seconds": result["wall_seconds"], "peak_rss_mib": result["peak_rss_mib"]})
            write_json(study/(window.window_id+".json"), result["window"])
            write_json(study/"progress.json", {"status": "RUNNING", "completed": performance})
        report = {"status": "PASS", "protocol": protocol, "results": summarize(catalogue, rows, *stage_args),
                  "performance": performance}
        if stage == 'vpin':
            report['vpin_prerequisite_sha256']=sha256_file(study/'vpin_prerequisite.json')
        if any(r["code_version"] != code for r in rows):
            raise ValueError("Study 1 runtime code changed after protocol freeze")
        write_json(study/"study1_report.json", report)
        write_json(study/"progress.json", {"status": "PASS", "completed": performance})
        return report
    except BaseException as exc:
        write_json(study/"progress.json", {"status": "FAIL", "completed": performance,
                   "error": f"{type(exc).__name__}: {exc}"})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/backtest/binance_recorded.yaml")
    parser.add_argument("--catalogue", required=True)
    parser.add_argument("--p0-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--stage", choices=("baselines", "bp", "vpin"), default="baselines")
    args = parser.parse_args()
    report = run(args.config, args.catalogue, args.p0_report, args.output, args.stage)
    print(json.dumps({"status": report["status"], "path": args.output}, sort_keys=True))
