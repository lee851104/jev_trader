"""Versioned manual replay; persist intent before any external decision request."""

import copy
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from .decision import build_request
from .engine import Account, equity, settle


class Conflict(ValueError):
    """A stale or unavailable operation; clients must refresh, never replay it."""


class Session:
    def __init__(self, dataset, decider, costs, store, start_at='replay'):
        if start_at not in ('latest', 'replay'):
            raise ValueError('請選擇最新行情判斷或歷史重播。')
        index = len(dataset.bars) - 1 if start_at == 'latest' else 19
        self.dataset, self.decider, self.costs, self.store = dataset, decider, costs, store
        self.lock = threading.RLock()
        self.state = dict(
            run_id=uuid.uuid4().hex, created_at=datetime.now(timezone.utc).isoformat(),
            source=dataset.source, digest=dataset.digest, mode=decider.mode,
            symbol=dataset.symbol, start_at=start_at,
            data_info=copy.deepcopy(dataset.metadata),
            model=decider.model, costs=dict(fee=str(costs.fee), slippage_bps=str(costs.slippage_bps)),
            initial_cash='10000.00', account=dict(cash='10000.00', shares=0),
            bars=[b.as_dict() for b in dataset.bars], index=index, date=dataset.bars[index].date,
            status='ready', version=0, pending=None, error=None, history=[], attempts=[],
            equity_history=[dict(date=dataset.bars[index].date, equity='10000.00')],
            model_calls=0, rule_decisions=0, latency_ms=None,
        )
        self._save(self.state)

    def snapshot(self):
        with self.lock:
            state = copy.deepcopy(self.state)
            state['can_decide'] = state['status'] == 'ready'
            state['can_advance'] = state['status'] == 'pending' and state['index'] + 1 < len(self.dataset.bars)
            return state

    def _save(self, state):
        try:
            self.store.write(state['run_id'], state)
        except (OSError, ValueError):
            self.state.update(status='error', error='紀錄寫入失敗；已停止，請重新開始。')
            self.state['version'] += 1
            raise ValueError('紀錄寫入失敗；已停止，未繼續交易。') from None
        self.state = state

    def _check(self, expected_version, status):
        if type(expected_version) is not int or expected_version != self.state['version']:
            raise Conflict('畫面已過期，請使用最新狀態。')
        if self.state['status'] != status:
            raise Conflict('目前無法執行此操作。')

    def _account(self):
        return Account(Decimal(self.state['account']['cash']), self.state['account']['shares'])

    def decide(self, expected_version):
        with self.lock:
            self._check(expected_version, 'ready')
            index = self.state['index']
            request = build_request(self.dataset.bars[index - 19:index + 1], self._account(),
                                    self.decider.model, self.dataset.symbol)
            request['state']['costs'] = self.state['costs'].copy()
            candidate = copy.deepcopy(self.state)
            candidate.update(status='requesting', version=candidate['version'] + 1)
            candidate['attempts'].append(dict(date=candidate['date'], request=request, status='started'))
            counter = 'model_calls' if self.decider.mode == 'jev' else 'rule_decisions'
            candidate[counter] += 1
            self._save(candidate)
            try:
                result = self.decider.decide(request)
            except Exception:
                candidate = copy.deepcopy(self.state)
                candidate.update(status='error', error='決策失敗；未重試，請匯出紀錄後重新開始。')
                candidate['attempts'][-1]['status'] = 'failed-or-unknown'
                candidate['version'] += 1
                self._save(candidate)
                return self.snapshot()
            candidate = copy.deepcopy(self.state)
            candidate['attempts'][-1]['status'] = 'completed'
            candidate['history'].append(dict(date=candidate['date'], decision=result))
            candidate.update(status='pending', pending=result['choice'], latency_ms=result['latency_ms'],
                             version=candidate['version'] + 1)
            self._save(candidate)
            return self.snapshot()

    def advance(self, expected_version):
        with self.lock:
            self._check(expected_version, 'pending')
            if self.state['index'] + 1 >= len(self.dataset.bars):
                raise Conflict('尚無下一交易日行情；這筆只提供判斷，不模擬未來成交。')
            candidate = copy.deepcopy(self.state)
            index = candidate['index'] + 1
            bar = self.dataset.bars[index]
            account, fill = settle(self._account(), candidate['pending'], bar, self.costs)
            value = str(equity(account, bar.close))
            candidate['account'] = dict(cash=str(account.cash), shares=account.shares)
            candidate['history'][-1].update(settlement=fill, account=candidate['account'].copy(), equity=value)
            candidate['equity_history'].append(dict(date=bar.date, equity=value))
            candidate.update(index=index, date=bar.date, pending=None,
                             status='finished' if index == len(self.dataset.bars) - 1 else 'ready',
                             version=candidate['version'] + 1)
            self._save(candidate)
            return self.snapshot()
