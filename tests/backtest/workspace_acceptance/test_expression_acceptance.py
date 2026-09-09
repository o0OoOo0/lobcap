"""R03–R05: independent numerical and adversarial expression expectations."""
from decimal import Decimal as D

import pytest

from lobcap.backtest.workspace.expressions import (
    compile_expression, ExpressionError, FeatureUnavailable,
)


def test_r03_manual_center_offset_decimal_arithmetic():
    expression = compile_expression(
        'strength * (microprice - mid) / tick - skew * inventory / max_inventory',
        parameter_names=('strength', 'skew'),
    )
    values = dict(strength=D(1), microprice=D('100.75'), mid=D('100.5'),
                  tick=D('.1'), skew=D(1), inventory=D('.002'), max_inventory=D('.01'))
    # 0.25 / 0.1 - 0.002 / 0.01 = 2.5 - 0.2 = 2.3 ticks.
    assert expression.evaluate(values) == D('2.3')
    assert compile_expression('0.1 + 0.2').evaluate({}) == D('0.3')
    assert compile_expression('clip(-3, -2, 2) + abs(-0.1)').evaluate({}) == D('-1.9')


@pytest.mark.parametrize('source', [
    'queue', 'alpha', 'future', 'replay_iterator', '__import__("os")',
    'mid.real', 'mid[0]', '[mid for _ in range(2)]', '(lambda: 1)()',
    'open("x")', '2 ** 100', 'mid > 0', 'True',
])
def test_r04_forbidden_syntax_and_execution_truth(source):
    with pytest.raises((ExpressionError, ValueError)):
        compile_expression(source)


def test_r04_reserved_parameter_and_complexity_rejected():
    with pytest.raises((ExpressionError, ValueError)):
        compile_expression('mid', parameter_names=('mid',))
    with pytest.raises((ExpressionError, ValueError)):
        compile_expression('1+' * 600 + '1')
    with pytest.raises((ExpressionError, ValueError)):
        compile_expression('0.12345678901234567890123456789')


def test_r04_missing_feature_is_not_runtime_arithmetic_error():
    with pytest.raises(FeatureUnavailable):
        compile_expression('volatility_bps + 1').evaluate({'volatility_bps': None})
    with pytest.raises(ExpressionError) as failure:
        compile_expression('1 / (mid - mid)').evaluate({'mid': D(100)})
    assert not isinstance(failure.value, FeatureUnavailable)


def test_r05_only_referenced_observations_affect_expression():
    expression = compile_expression('(microprice - mid) / tick')
    prefix = {'mid': D('100.5'), 'microprice': D('100.75'), 'tick': D('.1')}
    # Mutating an unrelated future diagnostic must not influence current signal.
    assert expression.evaluate(prefix) == D('2.5')
    assert expression.evaluate({**prefix, 'future': D('-100000')}) == D('2.5')
