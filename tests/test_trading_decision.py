import json
from decimal import Decimal

import httpx
import pytest

from jev_ultrafast.trading.data import demo_dataset
from jev_ultrafast.trading.decision import JevDecider, RuleDecider, build_request, validate_answer
from jev_ultrafast.trading.engine import Account


def answer():
    return dict(type='choice', choice='buy', probabilities=dict(buy=.7, sell=.1, hold=.2), confidence=.5)


@pytest.mark.parametrize('field,value', [
    ('choice', 'BUY'), ('confidence', True), ('confidence', float('nan')),
    ('probabilities', dict(buy=.1, sell=.6, hold=.3)), ('probabilities', dict(buy=1)),
])
def test_bad_answer(field, value):
    bad = answer()
    bad[field] = value
    with pytest.raises(ValueError):
        validate_answer(bad)


def test_request_and_real_http_contract():
    request = build_request(demo_dataset().bars[:20], Account(Decimal('10000'), 0), 'jev-latest')
    calls = []
    def respond(r):
        calls.append(r)
        assert r.url == 'https://api.typesafe.ai/v1/systemone'
        assert set(json.loads(r.content)['questions']['action']['criteria']) == {'buy', 'sell', 'hold'}
        return httpx.Response(200, json=dict(model='jev-test', answers={'action': answer()}, usage={}))
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = JevDecider('hidden-secret', 'jev-latest', client).decide(request)
    assert result['choice'] == 'buy'
    assert result['model'] == 'jev-test'
    assert len(calls) == 1
    assert 'hidden-secret' not in json.dumps(result)
    assert len(request['state']['bars']) == 20


@pytest.mark.parametrize('kind', ['timeout', '503', 'empty', 'text', 'secret'])
def test_failures_never_retry_or_expose_key(kind):
    calls = []
    def respond(r):
        calls.append(r)
        if kind == 'timeout':
            raise httpx.ReadTimeout('hidden-secret')
        if kind == '503':
            return httpx.Response(503, text='hidden-secret')
        if kind == 'text':
            return httpx.Response(200, text='hidden-secret')
        body = {} if kind == 'empty' else dict(model='hidden-secret', answers={'action': answer()}, usage={})
        return httpx.Response(200, json=body)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        decider = JevDecider('hidden-secret', 'jev-latest', client)
        if kind == 'secret':
            assert 'hidden-secret' not in json.dumps(decider.decide({}))
        else:
            with pytest.raises(ValueError) as error:
                decider.decide({})
            assert 'hidden-secret' not in str(error.value)
    assert len(calls) == 1


def test_offline_model_is_distinct():
    request = build_request(demo_dataset().bars[:20], Account(Decimal('10000'), 0), 'unused')
    result = RuleDecider().decide(request)
    assert result['model'] == 'offline-rule'
    assert result['choice'] in {'buy', 'sell', 'hold'}


def test_escaped_secret_is_redacted_after_decoding():
    raw = json.dumps(dict(model='jev-test', answers={'action': answer()},
                          usage={'hidden-secret': ['hidden-secret']}))
    raw = raw.replace('hidden-secret', r'\u0068idden-secret')
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=raw))) as client:
        result = JevDecider('hidden-secret', 'jev-latest', client).decide({})
    assert 'hidden-secret' not in json.dumps(result)
