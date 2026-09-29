import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

import pytest

from jev_ultrafast.trading.data import demo_dataset
from jev_ultrafast.trading.engine import Costs
from jev_ultrafast.trading.session import Conflict, Session
from jev_ultrafast.trading.storage import Store


class FixedDecider:
    mode = 'jev'
    model = 'test-only'

    def __init__(self, action='buy', fail=False):
        self.calls = []
        self.action, self.fail = action, fail

    def decide(self, request):
        self.calls.append(request)
        if self.fail:
            raise ValueError('sensitive-provider-error')
        return dict(choice=self.action, probabilities=None, confidence=None, model=self.model,
                    latency_ms=1, request=request, raw_response=None, usage={})


def make_session(tmp_path, action='buy', fail=False):
    return Session(demo_dataset(), FixedDecider(action, fail), Costs(D('1'), D('1')), Store(tmp_path))


def test_future_hidden_next_day_fill_and_export(tmp_path):
    session = make_session(tmp_path)
    initial = session.snapshot()
    result = session.decide(initial['version'])
    request = session.decider.calls[0]
    bars = request['state']['bars']
    assert len(bars) == 20
    assert bars[-1]['date'] == session.dataset.bars[19].date
    assert session.dataset.bars[20].date not in json.dumps(request)
    assert result['account'] == {'cash': '10000.00', 'shares': 0}
    final = session.advance(result['version'])
    assert final['date'] == session.dataset.bars[20].date
    assert final['account']['shares'] == 1
    assert final['history'][-1]['settlement']['status'] == 'filled'
    persisted = session.store.read(initial['run_id'])
    assert persisted['account'] == final['account']
    assert 'buy' in session.store.export_csv(initial['run_id'])
    assert len(session.store.list_runs()) == 1


def test_duplicate_and_concurrent_commands(tmp_path):
    session = make_session(tmp_path)
    def call():
        try:
            return session.decide(0)['status']
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(), range(2)))
    assert sorted(results) == ['conflict', 'pending']
    assert len(session.decider.calls) == 1
    pending = session.snapshot()
    session.advance(pending['version'])
    with pytest.raises(Conflict):
        session.advance(pending['version'])
    assert session.snapshot()['account']['shares'] == 1


def test_failure_blocks_future_paid_attempts(tmp_path):
    session = make_session(tmp_path, fail=True)
    state = session.decide(0)
    assert state['status'] == 'error'
    assert state['model_calls'] == 1
    assert 'sensitive' not in json.dumps(state)
    with pytest.raises(Conflict):
        session.decide(state['version'])
    assert len(session.decider.calls) == 1


@pytest.mark.parametrize('fail_on', [1, 2])
def test_storage_failure_stops_actions(tmp_path, monkeypatch, fail_on):
    session = make_session(tmp_path)
    write = session.store.write
    count = 0
    def failing_write(*args):
        nonlocal count
        count += 1
        if count == fail_on:
            raise OSError('disk unavailable')
        write(*args)
    monkeypatch.setattr(session.store, 'write', failing_write)
    with pytest.raises(ValueError):
        session.decide(0)
    state = session.snapshot()
    assert state['status'] == 'error'
    assert state['account']['shares'] == 0
    assert len(session.decider.calls) == fail_on - 1
    with pytest.raises(Conflict):
        session.advance(state['version'])


def test_hold_to_end_and_read_does_not_call_model(tmp_path):
    session = make_session(tmp_path, 'hold')
    while session.snapshot()['status'] != 'finished':
        state = session.decide(session.snapshot()['version'])
        session.advance(state['version'])
    assert session.snapshot()['account'] == {'cash': '10000.00', 'shares': 0}
    assert len(session.decider.calls) == 40
    with pytest.raises(Conflict):
        session.decide(session.snapshot()['version'])
    Store(tmp_path).read(session.snapshot()['run_id'])
    assert len(session.decider.calls) == 40


def test_paths_and_csv_formula(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.read('../secret')
    session = make_session(tmp_path)
    state = session.decide(0)
    session.advance(state['version'])
    state = store.read(state['run_id'])
    state['history'][0]['decision']['model'] = '=SUM(1,2)'
    store.write(state['run_id'], state)
    assert "'=SUM" in store.export_csv(state['run_id'])


def test_latest_symbol_decision_has_no_future_fill(tmp_path):
    from dataclasses import replace
    dataset = replace(demo_dataset(), symbol='AAPL')
    decider = FixedDecider()
    session = Session(dataset, decider, Costs(D('1'), D('1')), Store(tmp_path), start_at='latest')
    state = session.decide(0)
    assert state['symbol'] == 'AAPL'
    assert state['date'] == dataset.bars[-1].date
    assert decider.calls[0]['state']['symbol'] == 'AAPL'
    assert decider.calls[0]['state']['bars'] == [b.as_dict() for b in dataset.bars[-20:]]
    assert 'SPY' not in decider.calls[0]['questions']['action']['instructions']
    assert not state['can_advance']
    with pytest.raises(Conflict):
        session.advance(state['version'])
    assert session.snapshot()['account'] == {'cash': '10000.00', 'shares': 0}
    assert session.store.list_runs()[0]['symbol'] == 'AAPL'
    assert 'AAPL' in session.store.export_csv(state['run_id'])


def test_market_cache_rejects_cross_symbol_and_changed_prices(tmp_path):
    from dataclasses import replace
    data = replace(demo_dataset(), symbol='AAPL', source='yahoo-finance', metadata={'symbol': 'AAPL'})
    store = Store(tmp_path)
    store.cache_market(data)
    path = tmp_path / 'market-AAPL.json'
    original = json.loads(path.read_text(encoding='utf-8'))
    for replacement in [dict(original, symbol='MSFT'), dict(original, digest='invalid'),
                        dict(original, metadata={'symbol': 'MSFT'})]:
        path.write_text(json.dumps(replacement), encoding='utf-8')
        with pytest.raises(ValueError):
            store.load_market('AAPL')
    path.write_text(json.dumps(original), encoding='utf-8')
    assert store.load_market('AAPL').symbol == 'AAPL'
    assert store.load_market('AAPL').bars == data.bars


def test_old_spy_runs_still_export_with_symbol(tmp_path):
    session = make_session(tmp_path)
    state = session.decide(0)
    state.pop('symbol')
    session.store.write(state['run_id'], state)
    assert session.store.list_runs()[0]['symbol'] == 'SPY'
    assert 'SPY' in session.store.export_csv(state['run_id'])
