import json
import threading

import httpx
import pytest

from jev_ultrafast.trading.server import create_server


@pytest.fixture
def web(tmp_path):
    server = create_server(port=0, root=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    with httpx.Client(base_url=origin, trust_env=False) as client:
        yield client, {'Origin': origin, 'X-CSRF-Token': server.app.token}, server
    server.shutdown()
    server.server_close()
    thread.join()


def start(client, headers, **extra):
    return client.post('/api/start', headers=headers, json=dict(source='demo', mode='rule',
                       fee='1', slippage_bps='1', **extra))


def test_replay_script_is_served_before_app(web):
    client, _, _ = web
    page = client.get('/').text
    assert page.index('src="/replay.js"') < page.index('src="/app.js"')
    response = client.get('/replay.js')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/javascript')
    assert 'createReplay' in response.text


def test_full_flow_and_history(web):
    client, headers, server = web
    assert client.get('/').status_code == 200
    response = start(client, headers)
    assert response.status_code == 200
    state = response.json()['session']
    for endpoint in ['decide', 'advance']:
        response = client.post('/api/' + endpoint, headers=headers,
                               json=dict(run_id=state['run_id'], expected_version=state['version']))
        assert response.status_code == 200
        state = response.json()['session']
    assert len(state['history']) == 1
    assert len(state['equity_history']) == 2
    assert state['model_calls'] == 0
    exported = client.get('/api/export', params=dict(id=state['run_id'], format='json')).json()
    assert exported['account'] == state['account']
    assert server.app.token not in json.dumps(exported)
    csv = client.get('/api/export', params=dict(id=state['run_id'], format='csv'))
    assert 'decision_date' in csv.text
    assert len(client.get('/api/runs').json()['runs']) == 1


def test_request_guards(web):
    client, headers, _ = web
    assert client.get('/api/state', headers={'Host': 'evil.invalid'}).status_code == 403
    assert client.post('/api/start', json={}).status_code == 403
    bad = {**headers, 'Origin': 'https://evil.invalid'}
    assert client.post('/api/start', headers=bad, json={}).status_code == 403
    assert client.post('/api/start', headers=headers, content='{').status_code == 400
    assert client.post('/api/start', headers=headers, content='[]').status_code == 400
    assert client.post('/api/start', headers=headers, content='x' * (2 * 1024 * 1024 + 1)).status_code == 413
    assert client.get('/api/export?id=../secret&format=json').status_code == 400
    assert client.get('/.env').status_code == 404


def test_stale_run_and_version(web):
    client, headers, _ = web
    state = start(client, headers).json()['session']
    body = dict(run_id=state['run_id'], expected_version=0)
    assert client.post('/api/decide', headers=headers, json=body).status_code == 200
    assert client.post('/api/decide', headers=headers, json=body).status_code == 409
    start(client, headers)
    assert client.post('/api/decide', headers=headers, json=body).status_code == 409


def test_missing_keys_and_synthetic_jev_rejected(web, monkeypatch):
    client, headers, _ = web
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    monkeypatch.delenv('ALPHAVANTAGE_API_KEY', raising=False)
    assert client.post('/api/start', headers=headers, json=dict(source='demo', mode='jev')).status_code == 400
    response = client.post('/api/start', headers=headers, json=dict(source='alpha', mode='jev'))
    assert response.status_code == 400
    assert 'API_KEY' in response.json()['error']


def test_invalid_settings(web):
    client, headers, _ = web
    for body in [dict(source='demo', mode='rule', fee='NaN'), dict(source='url', mode='rule'),
                 dict(source='demo', mode='other'), dict(source='csv', mode='rule', csv_text='<script>')]:
        assert client.post('/api/start', headers=headers, json=body).status_code == 400


def test_spy_sample_and_api_mode_switch_preserve_per_decision_evidence(web, monkeypatch):
    from jev_ultrafast.trading.decision import JevDecider

    client, headers, _ = web
    offline = client.post('/api/start', headers=headers, json=dict(source='sample', mode='rule'))
    assert offline.status_code == 200
    offline_state = offline.json()['session']
    assert offline_state['source'] == 'yahoo-snapshot'
    assert offline_state['data_info']['symbol'] == 'SPY'
    assert offline_state['data_info']['provider'] == 'Yahoo Finance'
    assert len(offline_state['bars']) >= 21
    calls = []

    def respond(self, transport, request):
        calls.append(request)
        assert len(request['state']['bars']) == 20
        action = 'buy' if len(calls) == 1 else 'hold'
        answer = dict(type='choice', choice=action, confidence=.5,
                      probabilities={key: .8 if key == action else .1 for key in ['buy', 'sell', 'hold']})
        return httpx.Response(200, request=httpx.Request('POST', 'https://api.typesafe.ai/v1/systemone'),
                              json=dict(model='jev-mock', answers={'action': answer},
                                        usage=dict(input_tokens=1234, output_tokens=40)))

    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline-test-key')
    monkeypatch.setattr(JevDecider, '_post', respond)
    state = client.post('/api/start', headers=headers, json=dict(source='sample', mode='jev')).json()['session']
    assert not calls  # Selecting/loading API mode must not make an inference request.
    assert state['run_id'] != offline_state['run_id']
    for _ in range(2):
        for endpoint in ['decide', 'advance']:
            response = client.post('/api/' + endpoint, headers=headers,
                                   json=dict(run_id=state['run_id'], expected_version=state['version']))
            assert response.status_code == 200
            state = response.json()['session']
    assert len(calls) == state['model_calls'] == 2
    for event, action in zip(state['history'], ['buy', 'hold']):
        result = event['decision']
        assert result['raw_response']['answers']['action']['choice'] == action
        assert result['usage']['input_tokens'] == 1234
        assert result['request']['state']['bars'][-1]['date'] == event['date']
    assert client.get('/api/export', params=dict(id=offline_state['run_id'], format='json')).status_code == 200


def test_missing_jev_key_keeps_existing_real_data_run(web, monkeypatch):
    client, headers, _ = web
    before = client.post('/api/start', headers=headers, json=dict(source='sample', mode='rule'))
    assert before.status_code == 200
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    response = client.post('/api/start', headers=headers, json=dict(source='sample', mode='jev'))
    assert response.status_code == 400
    assert client.get('/api/state').json()['session']['run_id'] == before.json()['session']['run_id']


@pytest.mark.parametrize('content', ['x' * 131073, 'date,open,high,low,close,volume\n' + 'x' * 131073],
                         ids=['large-header', 'large-cell'])
def test_large_csv_field_returns_400_and_preserves_session(web, content):
    client, headers, _ = web
    before = start(client, headers).json()['session']
    response = client.post('/api/start', headers=headers, json=dict(source='csv', mode='rule', csv_text=content))
    assert response.status_code == 400
    assert client.get('/api/state').json()['session']['run_id'] == before['run_id']


def test_symbol_download_cache_and_request_stay_aligned(web, monkeypatch):
    from dataclasses import replace

    from jev_ultrafast.trading.data import demo_dataset
    from jev_ultrafast.trading.decision import JevDecider
    client, headers, _ = web
    downloads, requests = [], []
    def download(symbol, transport):
        downloads.append(symbol)
        return replace(demo_dataset(), symbol=symbol, source='yahoo-finance',
                       metadata={'symbol': symbol, 'currency': 'USD', 'provider': 'Yahoo Finance'})
    def decide(self, transport, request):
        requests.append(request)
        return httpx.Response(200, request=httpx.Request('POST', 'https://api.typesafe.ai/v1/systemone'),
                              json=dict(model='offline-test', answers={'action': dict(
                                  choice='hold', probabilities={'buy': .1, 'sell': .1, 'hold': .8}, confidence=.5)}))
    monkeypatch.setattr('jev_ultrafast.trading.server.download_market', download)
    monkeypatch.setattr(JevDecider, '_post', decide)
    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline-test-key')
    for symbol in ['AAPL', 'MSFT']:
        response = client.post('/api/start', headers=headers, json=dict(
            symbol=symbol.lower(), source='yahoo', mode='jev', start_at='latest'))
        assert response.status_code == 200
        state = response.json()['session']
        assert state['symbol'] == state['data_info']['symbol'] == symbol
        assert len(requests) == (0 if symbol == 'AAPL' else 1)
        state = client.post('/api/decide', headers=headers, json=dict(
            run_id=state['run_id'], expected_version=state['version'])).json()['session']
        assert state['history'][0]['decision']['request']['state']['symbol'] == symbol
        assert not state['can_advance']
    assert downloads == ['AAPL', 'MSFT']
    assert set(client.get('/api/state').json()['cached_symbols']) == {'AAPL', 'MSFT'}
    cached = client.post('/api/start', headers=headers, json=dict(symbol='AAPL', source='cache', mode='rule'))
    assert cached.status_code == 200
    assert cached.json()['session']['symbol'] == 'AAPL'
    assert cached.json()['session']['data_info']['symbol'] == 'AAPL'
    old_id = cached.json()['session']['run_id']
    for body in [dict(symbol='NVDA', source='cache'), dict(symbol='AAPL', source='sample'),
                 dict(symbol='../SPY', source='yahoo')]:
        response = client.post('/api/start', headers=headers, json=dict(mode='rule', **body))
        assert response.status_code == 400
        assert client.get('/api/state').json()['session']['run_id'] == old_id
    assert downloads == ['AAPL', 'MSFT']


def test_failed_download_keeps_loaded_stock(web, monkeypatch):
    client, headers, _ = web
    before = start(client, headers).json()['session']
    def fail(symbol, client):
        raise ValueError('行情來源暫時不可用')
    monkeypatch.setattr('jev_ultrafast.trading.server.download_market', fail)
    response = client.post('/api/start', headers=headers, json=dict(symbol='AAPL', source='yahoo', mode='rule'))
    assert response.status_code == 400
    assert client.get('/api/state').json()['session']['run_id'] == before['run_id']


def test_health_exposes_no_state_or_csrf(web):
    client, _, server = web
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    assert server.app.token not in response.text
