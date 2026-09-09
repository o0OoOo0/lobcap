from decimal import Decimal as D

import pytest

from lobcap.l2.fill_models import ModelSpec, advance_queue


@pytest.mark.parametrize("alpha,expected", [("0","0"),("0.5","0.5"),("1","1")])
def test_queue_arithmetic(alpha,expected):
    result=advance_queue(D(2),D(1),D(1),D(2),ModelSpec(alpha,D(alpha)))
    assert result.filled == D(expected)
    assert result.remaining + result.filled == 1


def test_nontrade_cannot_fill_and_touch_is_capped():
    model=ModelSpec("alpha=1",D(1))
    assert advance_queue(D(1),D(2),D(9),D(0),model).filled==0
    touch=advance_queue(D(99),D(2),D(0),D(8),ModelSpec("TOUCH_FILL",None,True))
    assert touch.filled==2 and touch.remaining==0
