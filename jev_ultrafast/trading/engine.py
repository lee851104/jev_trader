"""Pure, immutable account transitions using cent-rounded Decimal prices."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

ACTIONS = ('buy', 'sell', 'hold')


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Account:
    cash: Decimal
    shares: int


@dataclass(frozen=True)
class Costs:
    fee: Decimal
    slippage_bps: Decimal

    def __post_init__(self):
        if any(not n.is_finite() or not 0 <= n <= 100 for n in (self.fee, self.slippage_bps)):
            raise ValueError('費用須為 0–100 美元，滑價須為 0–100 基點。')
        object.__setattr__(self, 'fee', money(self.fee))


def equity(account: Account, close: Decimal) -> Decimal:
    return money(account.cash + account.shares * close)


def settle(account, action, bar, costs):
    if action not in ACTIONS:
        raise ValueError('決策只能為 buy、sell、hold。')
    record = dict(date=bar.date, action=action, status='held', quantity=0, price=None, fee='0.00', reason='持有')
    if action == 'hold':
        return account, record
    direction = 1 if action == 'buy' else -1
    price = money(bar.open * (1 + direction * costs.slippage_bps / Decimal(10000)))
    cash = money(account.cash - direction * price - costs.fee)
    if cash < 0 or (action == 'sell' and account.shares == 0) or price <= 0:
        record.update(status='rejected', reason='資金或持股不足，或成交價無效')
        return account, record
    record.update(status='filled', quantity=1, price=str(price), fee=str(costs.fee), reason='隔日開盤模擬成交')
    return Account(cash, account.shares + direction), record
