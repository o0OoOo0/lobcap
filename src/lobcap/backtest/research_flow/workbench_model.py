"""Observable strategy documentation and independently reconciled result views."""
from collections import deque,Counter
from decimal import Decimal as D,localcontext
from pathlib import Path

from .analysis import iter_rows

STRATEGIES={
 'fixed_spread':('고정 폭 양방향 호가','중간가격 주변에 일정한 간격으로 매수·매도를 제시합니다.',
    '의사결정 주기마다 midpoint − 반거리 / midpoint + 반거리를 계산합니다.',
    '양쪽 가격을 tick에 맞추고 현재 최우선 호가보다 공격적으로 진입하지 않습니다.',
    '폭이 작으면 가격변동 손실에 취약하고, 크면 체결이 줄거나 없어집니다.'),
 'inventory_skew':('재고에 따라 가격 이동','재고가 쌓인 방향의 추가 체결을 줄이려는 가격 정책입니다.',
    '매 주기 중심가격 = midpoint − 이동폭 × tick × 알려진 재고 / 한도.',
    '롱이면 중심을 내리고 숏이면 올립니다. 반올림과 BBO 제한으로 변화가 사라질 수 있습니다.',
    '재고를 즉시 청산하지 않습니다. 가격 이동이 한 tick보다 작으면 실효 변화가 없을 수 있습니다.'),
 'microprice':('호가 잔량 신호로 가격 이동','최우선 매수·매도 잔량으로 가중한 가격을 이용합니다.',
    '매 주기 중심 = midpoint + 신호 배수 × (microprice − midpoint).',
    '매수 잔량이 크면 중심이 올라가고 매도 잔량이 크면 내려갑니다.',
    '잔량 불균형은 미래 수익을 보장하지 않습니다. tick·BBO 제한 후 같은 주문이 될 수 있습니다.'),
 'join_bbo':('최우선 양방향 기준선','신호 없이 현재 최우선 매수·매도에 참여합니다.',
    '매 주기 전달된 best bid와 best ask를 그대로 목표 가격으로 사용합니다.',
    '설정 수량의 양방향 지정가 주문. 변경 시 기존 주문 취소를 기다린 뒤 다음 주기에 재주문합니다.',
    '호가 반거리·신호 배수는 사용하지 않습니다. 빠른 상대와 경쟁하며 불리할 때 체결될 수 있습니다.'),
 'inventory_size':('재고에 따라 수량 축소','가격은 BBO에 두고 재고를 늘리는 쪽 수량을 줄입니다.',
    '알려진 재고 / 한도가 +60% 이상이면 매수, −60% 이하면 매도 수량을 1 lot 줄입니다.',
    '기본 수량이 1 lot면 해당 방향의 주문을 철회합니다. 취소 효력 전에는 체결될 수 있습니다.',
    '60%는 고정된 정책입니다. 재고 평가손실 자체를 없애지는 않습니다.'),
 'flow_withdraw':('거래 흐름에 따라 한쪽 철회','최근 전달된 공격적 거래의 방향에 따라 한쪽 호가를 쉽니다.',
    '최근 1초 흐름 ≥ +0.6이면 매도, ≤ −0.6이면 매수 철회. 최소 거래량 조건도 충족해야 합니다.',
    '양의 방향은 +0.3 아래, 음의 방향은 −0.3 위에서 재개합니다. 관측량 부족 시 철회를 해제합니다.',
    '뒤늦게 도착한 데이터도 수신 시각 기준으로 반영합니다. 추세 중 남은 한쪽에서 재고가 쌓일 수 있습니다.'),
 'volatility_adaptive':('변동성에 따라 호가 폭 확대','최근 가격변동이 클 때 주문 거리를 넓힙니다.',
    '30개 과거 1초 수익률의 표준편차가 준비되면 고정 반거리와 변동성 거리 중 큰 값을 사용합니다.',
    '중심은 midpoint이며 변동성 × 가격 × 배수로 반거리를 조절합니다.',
    '신선한 관측이 부족하면 호가를 중단합니다. 넓어진 폭 때문에 체결이 없을 수 있습니다.'),
 'expression':('직접 작성하는 가격 신호','관측된 시장 값과 알려진 재고로 중심가격 이동량을 정의합니다.',
    '의사결정 주기마다 제한된 수식을 평가합니다. 출력 단위는 tick입니다.',
    '수식 출력 × tick을 midpoint에 더하고 출력 상한·반올림·가격 및 위험 제한을 적용합니다.',
    '수식은 주문 실행 코드가 아닙니다. 미래 값·큐 진실·임의 Python은 사용할 수 없습니다.'),
 'no_trade':('무거래 대조군','신규 주문을 전혀 내지 않는 계산 기준선입니다.',
    '매 주기 목표 매수·매도를 모두 비웁니다.',
    '초기 재고가 0이면 수수료와 매매손익도 0이어야 합니다.',
    '초기 재고가 있으면 거래가 없어도 평가손익이 생깁니다.'),
 'flow_guard':('복합 흐름 정책 · 이전 버전','microprice, 재고 이동, 변동성 거리와 흐름 철회를 결합한 기존 정책입니다.',
    '변동성이 준비되면 microprice 중심에서 재고 이동을 적용하고 흐름 임계값으로 한쪽을 철회합니다.',
    '반거리는 설정 거리와 양의 maker 비용 + 변동성 거리 중 큰 값입니다.',
    '여러 요소가 동시에 바뀌므로 단일 효과 비교에 부적합합니다. 비용 설정도 주문 행동을 바꿉니다.'),
}

METRICS={
 'net_pnl':'마지막 midpoint로 평가한 equity − 초기 equity. 모델 수수료 포함, 펀딩 제외. 종료 청산은 하지 않습니다.',
 'gross_pnl':'순손익 + 부호 있는 수수료. 수수료를 제외한 손익입니다.',
 'max_drawdown':'모든 원장 전이에서 계산한 equity 최고점 대비 최대 하락액. 아래 1초 그래프보다 정밀합니다.',
 'fill_rate':'한 번 이상 체결된 주문 수 / 활성화된 주문 수. 주문 단위이며 거래량 비율이 아닙니다.',
 'quantity_fill_rate':'체결 수량 합 / 활성화 주문의 최초 수량 합. 취소·재주문도 분모에 들어갑니다.',
 'turnover':'모든 체결 가격 × 체결 수량의 절댓값 합. 매수·매도 양쪽을 각각 셉니다.',
 'realized':'FIFO로 반대 방향 체결과 연결한 매매손익. 수수료 전이며 최초 재고는 표본 시작 midpoint를 기준으로 합니다.',
 'unrealized':'미청산 FIFO 잔량의 마지막 midpoint 평가손익. 실현 + 미실현 = 수수료 전 손익입니다.',
 'break_even_fee':'동일 체결 경로를 가정할 때 gross PnL / 체결 명목금액 × 10,000. 음수면 비용 0에서도 손실입니다. 전략 재실행 예측이 아닙니다.',
}


def economics(mid,tick,half_ticks,maker_bps):
    mid,tick,half_ticks,maker_bps=map(lambda x:D(str(x)),(mid,tick,half_ticks,maker_bps))
    if mid<=0:raise ValueError('positive reference midpoint required')
    return {'tick_usdt':tick,'tick_bps':tick/mid*10000,'half_usdt':tick*half_ticks,
            'half_bps':tick*half_ticks/mid*10000,'roundtrip_fee_bps':maker_bps*2,
            'roundtrip_fee_usdt_per_btc':mid*maker_bps*2/10000}


def policy_examples(config):
    """Small hypothetical states exercise the same policy API as the engine."""
    from lobcap.backtest.types import MarketView,PortfolioView
    from lobcap.backtest.strategies import StrategyParameters,decide,risk_control
    bid=D('80000');ask=bid+config.instrument.tick;mid=(bid+ask)/2
    rows=[]
    for label,inventory,imbalance,flow in [('중립',D(0),D(0),D(0)),
            ('롱 재고 60% · 매수 흐름',config.max_inventory*D('.6'),D('.8'),D('.8')),
            ('숏 재고 60% · 매도 흐름',-config.max_inventory*D('.6'),D('-.8'),D('-.8'))]:
        b=1+imbalance;a=1-imbalance
        market=MarketView(0,bid,ask,b,a,mid,(ask*b+bid*a)/(b+a),imbalance,1.,0,
            bid-D(1000),ask+D(1000),flow,D(1))
        portfolio=PortfolioView(config.initial_cash,inventory,config.initial_cash+inventory*mid)
        stages={}
        desired=decide(StrategyParameters.from_config(config),market,portfolio,trace=stages)
        final=risk_control(config,market,portfolio,desired)
        rows.append({'상태':label,'매수 가격':str(final.bid.price) if final.bid else '철회',
            '매수 BTC':str(final.bid.quantity) if final.bid else '0',
            '매도 가격':str(final.ask.price) if final.ask else '철회',
            '매도 BTC':str(final.ask.quantity) if final.ask else '0','판단':final.reason})
    return rows


def reviewed_metrics(run_dir):
    """FIFO is a second decomposition; never add it to spread+inventory MTM."""
    with localcontext() as ctx:
        ctx.prec=50
        first=last=None
        for row in iter_rows(run_dir,'ledger'):
            if first is None:first=row
            last=row
        if first is None:return {'available':False,'reason':'원장 미기록'}
        lots=deque()
        initial=D(first['inventory'])
        if initial:lots.append([1 if initial>0 else -1,abs(initial),D(first['mark_price'])])
        realized=notional=fees=quantity=D(0)
        count=0
        for fill in iter_rows(run_dir,'fills'):
            sign=1 if fill['side']=='buy' else -1
            price,left=D(fill['price']),D(fill['quantity'])
            quantity+=left;notional+=price*left;fees+=D(fill['fee']);count+=1
            while left and lots and lots[0][0]!=sign:
                side,held,cost=lots[0]
                closed=min(left,held)
                realized+=side*closed*(price-cost)
                left-=closed;lots[0][1]-=closed
                if not lots[0][1]:lots.popleft()
            if left:lots.append([sign,left,price])
        ending=D(last['mark_price'])
        unrealized=sum((s*q*(ending-p) for s,q,p in lots),D(0))
        gross=D(last['gross_pnl']);net=D(last['net_pnl'])
        inv=sum((s*q for s,q,p in lots),D(0))
        reconciled=(realized+unrealized==gross and gross-fees==net and inv==D(last['inventory']))
        flags=Counter();actions=Counter();decisions=0
        for trace in iter_rows(run_dir,'decision_traces'):
            decisions+=1
            flags.update(k for k,v in trace.get('stage_changes',{}).items() if v is True)
            actions.update(a['action'] for a in trace.get('actions',[]))
        return {'available':True,'reconciled':reconciled,'realized_gross':realized,
            'unrealized_gross':unrealized,'net_pnl':net,'gross_pnl':gross,'fees':fees,
            'turnover':notional,'filled_quantity':quantity,'fill_events':count,
            'break_even_fee_bps':gross/notional*10000 if notional else None,
            'net_bps_on_turnover':net/notional*10000 if notional else None,
            'return_on_initial_equity':net/D(first['equity']) if D(first['equity'])>0 else None,
            'decision_count':decisions,'stage_changes':dict(flags),'actions':dict(actions),
            'reference_mid':D(first['mark_price'])}
