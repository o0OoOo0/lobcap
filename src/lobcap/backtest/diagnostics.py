"""Bounded post-run explanations; diagnostic costs never silently alter the ledger."""
import gzip
import json
from collections import Counter
from decimal import Decimal, localcontext

from lobcap.backtest.replay import write_json
from lobcap.backtest.types import ZERO


def read_rows(path):
    if path.exists():
        with gzip.open(path,"rt") as stream:
            for line in stream:yield json.loads(line)


def build_diagnostics(path,config):
    with localcontext() as context:
        context.prec=50
        summary=json.loads((path/"summary.json").read_text())
        notional=quantity=shortfall=negative_capture_quantity=ZERO
        negative_capture_count=0
        fill_reasons=Counter()
        for fill in read_rows(path/"fills.jsonl.gz"):
            q,p=Decimal(fill["quantity"]),Decimal(fill["price"])
            notional+=q*p;quantity+=q
            decision=dict(fill["decision"])
            sign=1 if fill["side"]=="buy" else -1
            shortfall+=sign*q*(p-Decimal(decision["mid"]))
            if Decimal(fill["spread_contribution"])<0:
                negative_capture_count+=1;negative_capture_quantity+=q
            fill_reasons[fill["reason"]]+=1
        reasons=Counter(row["reason"] for row in read_rows(path/"decisions.jsonl.gz"))
        pnl={key:Decimal(summary[key]) for key in ("net_pnl","gross_pnl","spread_capture","inventory_mtm","fees")}
        components={"inventory_mtm":pnl["inventory_mtm"],"spread_capture":pnl["spread_capture"],"fees":-pnl["fees"]}
        driver=min(components,key=components.get) if pnl["net_pnl"]<0 else ("positive_sample_result" if pnl["net_pnl"]>0 else "zero_sample_result")
        stress=notional*config.impact_stress_bps/10000
        output={"status":"PASS","clock_mode":config.clock_mode,"report_ms":config.report_ms,
            "primary_loss_component":driver,"pnl_components":pnl,"filled_quantity":quantity,
            "turnover_notional":notional,"negative_capture_fills":negative_capture_count,
            "negative_capture_quantity":negative_capture_quantity,"fill_reasons":dict(fill_reasons),
            "decision_reasons":dict(reasons),"decision_mid_shortfall_usdt":shortfall,
            "decision_mid_shortfall_per_unit":shortfall/quantity if quantity else None,
            "shortfall_note":"Signed execution price minus decision midpoint; positive is worse. This benchmark is NOT an extra PnL deduction.",
            "break_even_maker_bps_fixed_fills":pnl["gross_pnl"]/notional*10000 if notional else None,
            "fee_scenarios_fixed_fills":[{"maker_bps":str(bps),"net_pnl":pnl["gross_pnl"]-notional*Decimal(str(bps))/10000}
                                        for bps in (-.5,-.1,0,.5,1,2,5)],
            "fee_scenario_note":"Reprices the SAME fills. A cost-aware strategy may change its quotes in a full fee sweep.",
            "impact_stress_bps":config.impact_stress_bps,"hypothetical_impact_cost":stress,
            "stress_adjusted_net_pnl":pnl["net_pnl"]-stress,
            "impact_note":"Uncalibrated fixed-notional cost stress, NOT endogenous market impact. Historical prices/depth remain unchanged.",
            "max_trade_participation":config.max_trade_participation,
            "validation_scope":"Engine accounting and execution invariants checked. Source-linked evidence is available; independent source audit is a separate validation step. Fill realism is uncalibrated.",
            "markouts":summary["markouts"],"sample_minutes":(config.sample.end_ns-config.sample.start_ns)/60_000_000_000}
        write_json(path/"diagnostics.json",output)
        return output
