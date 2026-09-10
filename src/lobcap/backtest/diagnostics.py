"""Bounded post-run explanations; diagnostic costs never silently alter the ledger."""
import gzip
import hashlib
import json
from collections import Counter
from decimal import Decimal, localcontext

from lobcap.backtest.replay import write_json
from lobcap.backtest.types import ZERO, EXECUTION_CONTRACT_VERSION


def quote_path_diagnostics(rows):
    """Compare actual desired quotes, excluding strategy names and explanations."""
    digest=hashlib.sha256()
    count=changed=at_bbo=quoted_sides=0
    previous=None
    for row in rows:
        quotes=tuple(row.get(k) for k in ("buy_price","buy_quantity","sell_price","sell_quantity"))
        # Normalize Decimal spelling so economically equal quotes have equal IDs.
        quotes=tuple(format(Decimal(str(v)).normalize(),"f") if v is not None else None for v in quotes)
        digest.update((json.dumps([row["time_ns"],*quotes],separators=(",", ":"))+"\n").encode())
        count+=1
        changed+=int(previous is not None and previous!=quotes)
        previous=quotes
        for price,bbo in (("buy_price","bid"),("sell_price","ask")):
            if row.get(price) is not None:
                quoted_sides+=1
                at_bbo+=int(Decimal(str(row[price]))==Decimal(str(row[bbo])))
    return {"quote_path_sha256":digest.hexdigest(),"decision_count":count,
            "quote_changes":changed,"quoted_sides":quoted_sides,"at_bbo_sides":at_bbo,
            "at_bbo_fraction":Decimal(at_bbo)/quoted_sides if quoted_sides else None,
            "note":"Identical desired-quote paths on this sample are not independent strategies. At-BBO share is not a measured clamp rate."}


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
            "execution_contract_version":EXECUTION_CONTRACT_VERSION,
            "execution_contract":{"own_fill_lots":"instrument lot on every path",
                "trade_through":"clear inferred ahead queue; cap fills by current normal trade volume",
                "participation":"cumulative eligible volume per own order; no transfer to replacement",
                "bbo_only_fill":False,"empirical_fill_calibration":False},
            "quote_research":quote_path_diagnostics(read_rows(path/"decisions.jsonl.gz")),
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
