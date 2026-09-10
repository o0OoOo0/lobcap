from decimal import Decimal as D
import pytest

from lobcap.backtest.diagnostics import quote_path_diagnostics
from lobcap.backtest.workspace.compare import compare_rows


def test_quote_identity_excludes_strategy_labels_but_includes_timing_and_size():
    row=dict(time_ns=1,buy_price='100.0',sell_price='101',buy_quantity='.001',sell_quantity='.001',bid='100',ask='101',strategy='one')
    base=quote_path_diagnostics([row])
    assert base['at_bbo_fraction']==D(1)
    assert base['quote_path_sha256']==quote_path_diagnostics([{**row,'strategy':'two','buy_price':'100.00'}])['quote_path_sha256']
    for patch in ({'time_ns':2},{'buy_quantity':'.002'},{'buy_price':None,'buy_quantity':'0'}):
        assert base['quote_path_sha256']!=quote_path_diagnostics([{**row,**patch}])['quote_path_sha256']


def test_no_quotes_have_no_bbo_fraction():
    assert quote_path_diagnostics([dict(time_ns=1)])['at_bbo_fraction'] is None


def test_engine_versions_cannot_mix_even_in_strategy_sensitivity():
    base=dict(sample_sha256='same',strategy_id='same',scenario_id='same')
    with pytest.raises(ValueError,match='contract versions'):
        compare_rows([base,{**base,'execution_contract_version':'binance-own-orders-v2'}],strategy_id='same')
