"""
l3_queue.py — Coinbase L3(주문 단위) 데이터로 큐 위치 "정답"을 만든다

이 프로젝트에서 가장 중요한 스크립트다.

기존 계획의 한계
    L2 데이터만으로는 내 주문이 큐의 몇 번째인지 알 수 없다.
    → 비관/중립/낙관 3모드를 "가정"하고 민감도만 볼 수밖에 없었다.

Coinbase full 채널이 바꾸는 것
    모든 개별 주문의 order_id, 가격, 수량, 그리고 주문이 북에 올라온 순서를 다 준다.
    → 시각 t에 가격 p에 주문을 냈다면 내 앞에 정확히 어떤 주문들이 있었고,
      그것들이 언제 체결/취소됐는지 "정답"을 계산할 수 있다.
    → 가정이 아니라 측정. L2 기반 큐 모델을 이 정답에 대고 캘리브레이션한다.

Coinbase full 채널 메시지 타입
    received : 주문 접수 (아직 북에 없음 — 북을 바꾸지 않는다)
    open     : 주문이 북에 올라감 (여기서 큐 순위 확정)
    match    : 체결 — maker_order_id 가 체결된 지정가 주문
    done     : 주문 종료 (reason: filled | canceled)
    change   : 수량 변경 (감소만 가능, 가격-시간 우선순위는 유지)

매칭 엔진은 price-time priority FIFO 이므로, open 메시지 도착 순서가 곧 큐 순서다.
"""

from collections import OrderedDict, defaultdict
from decimal import Decimal


class L3Book:
    """주문 단위 호가창. 가격 레벨마다 order_id의 FIFO 큐를 유지한다."""

    def __init__(self):
        self.orders = {}                      # order_id -> dict(side, price, size)
        self.levels = defaultdict(OrderedDict)  # (side, price) -> OrderedDict[order_id -> size]
        self.seq = None

    # ── 메시지 적용 ──────────────────────────────────────────
    def apply(self, m: dict):
        t = m.get("type")
        self.seq = m.get("sequence", self.seq)

        if t == "open":
            # remaining_size 가 실제로 북에 올라간 수량
            oid = m["order_id"]
            side = m["side"]
            px = Decimal(m["price"])
            sz = Decimal(m["remaining_size"])
            if sz <= 0:
                return
            self.orders[oid] = {"side": side, "price": px, "size": sz}
            self.levels[(side, px)][oid] = sz     # OrderedDict → 삽입 순서 = 큐 순서

        elif t == "match":
            # maker 쪽 주문이 (부분) 체결됨
            oid = m.get("maker_order_id")
            if oid is None or oid not in self.orders:
                return
            sz = Decimal(m["size"])
            o = self.orders[oid]
            o["size"] -= sz
            lvl = self.levels[(o["side"], o["price"])]
            if o["size"] <= 0:
                lvl.pop(oid, None)
                self.orders.pop(oid, None)
            else:
                lvl[oid] = o["size"]              # 값만 갱신 → 순서 유지

        elif t == "done":
            oid = m["order_id"]
            o = self.orders.pop(oid, None)
            if o is None:
                return                            # 북에 없던 주문 (IOC 등)
            self.levels[(o["side"], o["price"])].pop(oid, None)

        elif t == "change":
            # 수량 감소. FIFO 우선순위는 유지된다 (증가는 불가)
            oid = m["order_id"]
            o = self.orders.get(oid)
            if o is None or "new_size" not in m:
                return
            new = Decimal(m["new_size"])
            o["size"] = new
            lvl = self.levels[(o["side"], o["price"])]
            if new <= 0:
                lvl.pop(oid, None)
                self.orders.pop(oid, None)
            else:
                lvl[oid] = new

    # ── 조회 ────────────────────────────────────────────────
    def level_queue(self, side, price):
        """해당 레벨의 (order_id, size) FIFO 리스트."""
        return list(self.levels[(side, price)].items())

    def level_volume(self, side, price):
        return sum(self.levels[(side, price)].values())

    def best(self, side):
        prices = [p for (s, p), q in self.levels.items() if s == side and q]
        if not prices:
            return None
        return max(prices) if side == "buy" else min(prices)


class VirtualOrder:
    """실제로 내지 않은 가상 주문. 큐 맨 뒤에 세우고 운명을 추적한다.

    L3에서는 "내 앞에 있던 주문들"이 정확히 식별되므로,
    그것들이 체결/취소되는 것을 그대로 세면 된다.
    """

    __slots__ = ("side", "price", "size", "t_place", "ahead_ids", "ahead_vol0",
                 "filled_ahead", "canceled_ahead", "t_fill", "outcome")

    def __init__(self, book: L3Book, side, price, size, t_place):
        self.side = side
        self.price = price
        self.size = Decimal(str(size))
        self.t_place = t_place
        # 배치 시점의 큐 스냅샷 = 내 앞의 주문 집합
        q = book.level_queue(side, price)
        self.ahead_ids = {oid: sz for oid, sz in q}
        self.ahead_vol0 = sum(self.ahead_ids.values())
        self.filled_ahead = Decimal(0)
        self.canceled_ahead = Decimal(0)
        self.t_fill = None
        self.outcome = "open"

    def ahead_remaining(self) -> Decimal:
        return sum(self.ahead_ids.values())

    def observe(self, m: dict, book: L3Book, t):
        """메시지를 보고 내 앞 물량이 어떻게 줄었는지 기록."""
        if self.outcome != "open":
            return

        t_ = m.get("type")

        if t_ == "match":
            oid = m.get("maker_order_id")
            if oid in self.ahead_ids:
                sz = Decimal(m["size"])
                take = min(sz, self.ahead_ids[oid])
                self.ahead_ids[oid] -= take
                self.filled_ahead += take
                if self.ahead_ids[oid] <= 0:
                    del self.ahead_ids[oid]
            # 내 앞이 다 빠졌는데 또 체결이 오면 내 차례
            if not self.ahead_ids and m.get("price") is not None:
                if Decimal(m["price"]) == self.price:
                    self.outcome = "filled"
                    self.t_fill = t

        elif t_ == "done":
            oid = m["order_id"]
            if oid in self.ahead_ids:
                rem = self.ahead_ids.pop(oid)
                if m.get("reason") == "canceled":
                    self.canceled_ahead += rem
                else:
                    self.filled_ahead += rem

        elif t_ == "change":
            oid = m["order_id"]
            if oid in self.ahead_ids and "new_size" in m:
                new = Decimal(m["new_size"])
                delta = self.ahead_ids[oid] - new
                if delta > 0:
                    self.canceled_ahead += delta
                self.ahead_ids[oid] = new
                if new <= 0:
                    del self.ahead_ids[oid]

    def report(self):
        return {
            "side": self.side,
            "price": str(self.price),
            "size": str(self.size),
            "queue_ahead_at_place": str(self.ahead_vol0),
            "queue_ahead_remaining": str(self.ahead_remaining()),
            "filled_ahead": str(self.filled_ahead),
            "canceled_ahead": str(self.canceled_ahead),
            "outcome": self.outcome,
            "t_place": self.t_place,
            "t_fill": self.t_fill,
        }


# ─────────────────────────────────────────────────────────────
# L2 가정 모델과 정답 비교
# ─────────────────────────────────────────────────────────────
def l2_model_predict(mode: str, ahead_vol0: Decimal,
                     level_vol_decrease: Decimal,
                     trade_vol_at_level: Decimal) -> bool:
    """L2만 보고 '체결됐다'고 판정할지 예측.

    mode
      optimistic : 가격이 닿으면(= 해당 레벨에 체결이 발생하면) 체결로 본다
      neutral    : 레벨 감소량을 체결/취소 비율로 안분해 앞 물량 차감
      pessimistic: 체결량만으로 앞 물량을 차감 (취소는 내 앞을 줄여주지 않음)
    """
    if mode == "optimistic":
        return trade_vol_at_level > 0
    if mode == "pessimistic":
        return trade_vol_at_level >= ahead_vol0
    if mode == "neutral":
        # 감소분 전체가 앞을 줄여준다고 본다 (취소도 포함)
        return level_vol_decrease >= ahead_vol0
    raise ValueError(mode)


def confusion(records):
    """정답(L3) vs 예측(L2 모드)의 혼동행렬."""
    out = {}
    for mode in ("optimistic", "neutral", "pessimistic"):
        tp = fp = fn = tn = 0
        for r in records:
            truth = r["truth_filled"]
            pred = r[f"pred_{mode}"]
            if truth and pred:
                tp += 1
            elif not truth and pred:
                fp += 1      # 존재하지 않는 체결 = 백테스트에만 있는 수익
            elif truth and not pred:
                fn += 1      # 놓친 체결 = 과도하게 보수적
            else:
                tn += 1
        n = max(tp + fp + fn + tn, 1)
        out[mode] = {
            "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "accuracy": round((tp + tn) / n, 4),
            "false_fill_rate": round(fp / max(fp + tn, 1), 4),
            "missed_fill_rate": round(fn / max(fn + tp, 1), 4),
        }
    return out


if __name__ == "__main__":
    # 합성 L3 메시지로 로직 검증
    msgs = [
        {"type": "open", "order_id": "A", "side": "buy", "price": "100.00",
         "remaining_size": "5.0", "sequence": 1},
        {"type": "open", "order_id": "B", "side": "buy", "price": "100.00",
         "remaining_size": "3.0", "sequence": 2},
        # ── 여기서 가상 주문 배치 (앞에 A=5, B=3 → 8) ──
        {"type": "match", "maker_order_id": "A", "size": "5.0",
         "price": "100.00", "side": "buy", "sequence": 3},
        {"type": "done", "order_id": "A", "reason": "filled", "sequence": 4},
        {"type": "done", "order_id": "B", "reason": "canceled",
         "sequence": 5},
        {"type": "match", "maker_order_id": "C", "size": "1.0",
         "price": "100.00", "side": "buy", "sequence": 6},
    ]

    book = L3Book()
    vo = None
    for i, m in enumerate(msgs):
        if i == 2:
            vo = VirtualOrder(book, "buy", Decimal("100.00"), 1.0, t_place=i)
            print("가상 주문 배치 — 앞 물량:", vo.ahead_vol0)
        if vo:
            vo.observe(m, book, t=i)
        book.apply(m)

    print("결과:", vo.report())

    # L2 모드 예측과 비교
    #   실제로는 A(5.0)만 체결되고 B(3.0)는 취소됨.
    #   체결량 5.0 < 앞 물량 8.0 → 비관 모드는 "체결 안 됨" 예측
    #   감소량 8.0 >= 8.0        → 중립 모드는 "체결됨" 예측
    rec = {
        "truth_filled": vo.outcome == "filled",
        "pred_optimistic": l2_model_predict("optimistic", Decimal("8"),
                                            Decimal("8"), Decimal("5")),
        "pred_neutral": l2_model_predict("neutral", Decimal("8"),
                                         Decimal("8"), Decimal("5")),
        "pred_pessimistic": l2_model_predict("pessimistic", Decimal("8"),
                                             Decimal("8"), Decimal("5")),
    }
    print("정답:", rec["truth_filled"])
    for k in ("optimistic", "neutral", "pessimistic"):
        print(f"  {k:12s} 예측: {rec['pred_' + k]}")
    print("\n혼동행렬(1건):", confusion([rec]))
