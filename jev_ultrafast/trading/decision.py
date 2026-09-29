"""A single typed Jev choice per explicit user request; never retry automatically."""

import json
import math
import time
from decimal import Decimal

import httpx

from .data import normalize_symbol
from .engine import ACTIONS


def build_request(bars, account, model, symbol='SPY'):
    symbol = normalize_symbol(symbol)
    if len(bars) != 20:
        raise ValueError('決策需要恰好 20 筆已發生的日線。')
    return dict(model=model, state={
        'symbol': symbol, 'bars': [b.as_dict() for b in bars],
        'account': dict(cash=str(account.cash), shares=account.shares),
    }, questions={'action': {
        'type': 'choice',
        'instructions': (
            f'Choose the next action for a {symbol} paper-trading experiment using only the supplied daily bars. '
            'One share at the next trading day open, maximum one action per day. No borrowing or short selling. '
            'Hold keeps the current position, including zero shares. Prefer hold when evidence is insufficient. '
            'Evaluate a short-term one-trading-day horizon, accounting for the supplied transaction costs. '
            'Do not use remembered future prices or events. Data is unadjusted; this is not investment advice.'
        ),
        'criteria': {'buy': 'Buy one share if cash permits.', 'sell': 'Sell one existing share.',
                     'hold': 'Keep cash and shares unchanged; no trade.'},
    }})


def validate_answer(answer):
    try:
        probabilities = answer['probabilities']
        numbers = [*probabilities.values(), answer['confidence']]
        valid = (
            answer.get('type', 'choice') == 'choice'
            and answer['choice'] in ACTIONS and set(probabilities) == set(ACTIONS)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < .02
            and probabilities[answer['choice']] >= max(probabilities.values()) - 1e-6
        )
    except (TypeError, KeyError, AttributeError):
        valid = False
    if not valid:
        raise ValueError('Jev 回應格式不正確，未建立交易。')
    return answer


class JevDecider:
    mode = 'jev'

    def __init__(self, key, model='jev-latest', client=None):
        if not key.strip():
            raise ValueError('請在伺服器 .env 設定 TYPESAFE_API_KEY。')
        self.key, self.model, self.client = key, model, client

    def decide(self, request):
        started = time.perf_counter()
        try:
            if self.client is None:
                with httpx.Client(timeout=25, trust_env=False) as client:
                    response = self._post(client, request)
            else:
                response = self._post(self.client, request)
            response.raise_for_status()
            # Decode first: JSON unicode escapes must not bypass credential redaction.
            raw = self._redact(response.json())
            json.dumps(raw, allow_nan=False)
            answer = validate_answer(raw['answers']['action'])
            if not isinstance(raw['model'], str) or not raw['model']:
                raise ValueError()
            return dict(choice=answer['choice'], probabilities=answer['probabilities'],
                        confidence=answer['confidence'], model=raw['model'], raw_response=raw,
                        request=request, latency_ms=round((time.perf_counter() - started) * 1000),
                        usage=raw.get('usage', {}))
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ValueError('Jev 請求失敗或回應無效；未重送，請保存紀錄後重新開始。') from None

    def _post(self, client, request):
        return client.post('https://api.typesafe.ai/v1/systemone', json=request,
                           headers={'Authorization': f'Bearer {self.key}'}, timeout=25)

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self.key, '[REDACTED]')
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        return value


class RuleDecider:
    mode = 'rule'
    model = 'offline-rule'

    def decide(self, request):
        state = request['state']
        closes = [Decimal(b['close']) for b in state['bars'][-5:]]
        mean = sum(closes) / len(closes)
        choice = 'hold'
        if closes[-1] > mean:
            choice = 'buy'
        elif closes[-1] < mean and state['account']['shares'] > 0:
            choice = 'sell'
        return dict(choice=choice, probabilities=None, confidence=None, model=self.model,
                    raw_response=None, request=request, latency_ms=0, usage={})
