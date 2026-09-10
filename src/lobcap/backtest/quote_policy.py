"""Pure lifecycle policy over acknowledged order records, shared by both clocks."""
from decimal import Decimal as D


VERSION='quote-policy-v1'


def plan_actions(config, desired, orders, now, market=None, portfolio=None, next_order_id=0):
    actions=[]
    for side,quote in (('buy',desired.bid),('sell',desired.ask)):
        current=orders.get(side)
        action,reason='none','no_target'
        oid=current['order_id'] if current else None
        if current is None and quote is not None:
            next_order_id+=1
            action,reason,oid='submit','new_target',f'order-{next_order_id:08d}'
        elif current is not None:
            price,remaining,original=(D(current[k]) for k in ('price','remaining','quantity'))
            if current['status']=='CANCEL_PENDING':action,reason='retain','cancel_pending'
            elif quote is None:action,reason='cancel',desired.reason
            else:
                unsafe=False
                if market is not None:
                    unsafe=(not market.coverage_bid<=price<=market.coverage_ask or
                            (price>=market.ask if side=='buy' else price<=market.bid))
                if portfolio is not None:
                    unsafe=unsafe or (portfolio.inventory+remaining>config.max_inventory if side=='buy'
                                      else portfolio.inventory-remaining < -config.max_inventory)
                partial=remaining<original
                keep_partial=(config.partial_fill_policy=='keep_remaining' and partial and quote.quantity==original)
                shrink=(config.partial_fill_policy=='keep_remaining' and partial and quote.quantity<original)
                size_change=(quote.quantity!=remaining and not keep_partial) or shrink
                delta=abs(quote.price-price)/config.instrument.tick
                started=current.get('activation_ns')
                if started is None:started=current['submitted_ns']
                if unsafe:action,reason='cancel','retained_order_risk'
                elif size_change:action,reason='cancel','target_size_changed'
                elif delta==0:action,reason='retain','keep_partial' if keep_partial else 'unchanged'
                elif delta<config.reprice_threshold_ticks:action,reason='retain','below_reprice_threshold'
                elif now-started<config.min_hold_ms*1_000_000:action,reason='retain','minimum_hold'
                else:action,reason='cancel','replace'
        actions.append({'side':side,'action':action,'reason':reason,'order_id':oid})
    return actions


def make_trace(config, ordinal, market, portfolio, stages, desired, actions, source=None, policy_state=None):
    from lobcap.backtest.types import jsonable
    def shape(target):
        row=jsonable(target)
        def quote(q):return None if q is None else (q['side'],D(q['price']),D(q['quantity']))
        return None if row is None else (quote(row['bid']),quote(row['ask']))
    return jsonable({'trace_version':'decision-trace-v1','policy_version':VERSION,
        'decision_id':f'decision-{ordinal:08d}','time_ns':market.time_ns,'clock_mode':config.clock_mode,
        'observable_market':market,'observable_portfolio':portfolio,
        **stages,'risk_limited_target':desired,'actions':actions,
        'stage_changes':{
            'tick_rounding':None if stages.get('tick_rounded_target') is None else shape(stages.get('raw_target'))!=shape(stages.get('tick_rounded_target')),
            'price_policy':None if stages.get('tick_rounded_target') is None else shape(stages.get('tick_rounded_target'))!=shape(stages.get('policy_limited_target')),
            'risk':shape(stages.get('policy_limited_target'))!=shape(desired)},
        'replay_index':source.replay_index if source else None,
        'source_refs':source.source_refs if source else (), 'policy_state':policy_state or {}})


def custom_stages(desired):
    # An external strategy callback is opaque: don't fabricate internal stages.
    return dict(raw_target=desired,tick_rounded_target=None,policy_limited_target=desired,
                target_stage_source='custom_callback_internal_stages_unavailable')
