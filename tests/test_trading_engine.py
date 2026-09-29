from decimal import Decimal as D

import pytest

from jev_ultrafast.trading.data import Bar
from jev_ultrafast.trading.engine import Account, Costs, equity, settle

BAR = Bar('2026-01-02', D('100'), D('110'), D('90'), D('105'), 100)


@pytest.mark.parametrize('action,cash,shares,want_cash,want_shares,status', [
    ('buy', '10000', 0, '9898.99', 1, 'filled'),
    ('sell', '10000', 1, '10098.99', 0, 'filled'),
    ('hold', '10000', 0, '10000', 0, 'held'),
    ('hold', '10000', 1, '10000', 1, 'held'),
    ('buy', '100', 0, '100', 0, 'rejected'),
    ('sell', '10000', 0, '10000', 0, 'rejected'),
])
def test_settlement(action, cash, shares, want_cash, want_shares, status):
    before = Account(D(cash), shares)
    after, record = settle(before, action, BAR, Costs(D('1'), D('1')))
    assert after == Account(D(want_cash), want_shares)
    assert record['status'] == status
    assert before.cash == D(cash)
    if status != 'filled':
        assert record['fee'] == '0.00'


def test_equity_and_invalid_action():
    assert equity(Account(D('100'), 2), D('105')) == D('310.00')
    with pytest.raises(ValueError):
        settle(Account(D('100'), 0), 'BUY', BAR, Costs(D('1'), D('1')))


@pytest.mark.parametrize('fee,slip', [('NaN', '1'), ('-1', '1'), ('1', 'Infinity'), ('101', '1')])
def test_invalid_costs(fee, slip):
    with pytest.raises(ValueError):
        Costs(D(fee), D(slip))


def test_sell_cannot_create_negative_cash():
    tiny = Bar('2026-01-02', D('.1'), D('.1'), D('.1'), D('.1'), 1)
    before = Account(D('0'), 1)
    after, record = settle(before, 'sell', tiny, Costs(D('1'), D('1')))
    assert after == before
    assert record['status'] == 'rejected'
