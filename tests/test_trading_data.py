from decimal import Decimal

import httpx
import pytest

from jev_ultrafast.trading.data import demo_dataset, download_spy, parse_csv, to_csv


def test_demo_roundtrip_and_bom():
    data = demo_dataset()
    assert data.source == 'synthetic'
    assert len(data.bars) == 60
    loaded = parse_csv('\ufeff' + to_csv(data))
    assert loaded.bars == data.bars
    assert loaded.digest == data.digest
    assert loaded.bars[0].open > Decimal('0')


@pytest.mark.parametrize('replacement', ['NaN', 'Infinity', '-1', '0'])
def test_invalid_price(replacement):
    text = to_csv(demo_dataset())
    rows = text.splitlines()
    fields = rows[1].split(',')
    fields[1] = replacement
    rows[1] = ','.join(fields)
    with pytest.raises(ValueError):
        parse_csv('\n'.join(rows))


def test_duplicate_unsorted_short_and_bad_ohlc():
    rows = to_csv(demo_dataset()).splitlines()
    for bad in [rows[:2], rows + [rows[1]], [rows[0], *reversed(rows[1:])]]:
        with pytest.raises(ValueError):
            parse_csv('\n'.join(bad))
    fields = rows[1].split(',')
    fields[3] = '99999'
    rows[1] = ','.join(fields)
    with pytest.raises(ValueError):
        parse_csv('\n'.join(rows))


@pytest.mark.parametrize('volume', ['-1', '1.5', 'NaN'])
def test_bad_volume(volume):
    rows = to_csv(demo_dataset()).splitlines()
    fields = rows[1].split(',')
    fields[5] = volume
    rows[1] = ','.join(fields)
    with pytest.raises(ValueError):
        parse_csv('\n'.join(rows))


def test_download_normalizes_descending_data():
    data = demo_dataset()
    def respond(request):
        assert request.url.params['symbol'] == 'SPY'
        return httpx.Response(200, json={'Meta Data': {'2. Symbol': 'SPY'}, 'Time Series (Daily)': {
            b.date: dict(zip(['1. open', '2. high', '3. low', '4. close', '5. volume'],
                            [str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume)]))
            for b in reversed(data.bars)
        }})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = download_spy('secret', client)
    assert result.bars == data.bars
    assert result.source == 'alpha-vantage'


@pytest.mark.parametrize('status,body', [(429, {}), (200, {'Information': 'secret'}), (200, [])])
def test_provider_error_is_safe(status, body):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))) as client:
        with pytest.raises(ValueError) as error:
            download_spy('secret', client)
        assert 'secret' not in str(error.value)


@pytest.mark.parametrize('symbol', ['../AAPL', 'AAPL?x=y', '', '^GSPC', 'AAPL/USD', 123])
def test_invalid_ticker_cannot_be_used_as_url_or_cache_path(symbol):
    from jev_ultrafast.trading.data import normalize_symbol
    with pytest.raises(ValueError):
        normalize_symbol(symbol)


def yahoo_fixture(symbol='AAPL'):
    from datetime import datetime, timezone
    bars = demo_dataset().bars[:22]
    return {'chart': {'error': None, 'result': [{
        'meta': {'symbol': symbol, 'currency': 'USD', 'instrumentType': 'EQUITY',
                 'exchangeTimezoneName': 'America/New_York'},
        'timestamp': [int(datetime.fromisoformat(b.date).replace(tzinfo=timezone.utc).timestamp()) for b in bars],
        'indicators': {'quote': [{field: [float(getattr(b, field)) if field != 'volume' else b.volume for b in bars]
                                  for field in ['open', 'high', 'low', 'close', 'volume']}]},
    }]}}


def test_yahoo_symbol_identity_complete_bars_and_provenance():
    from datetime import datetime, timezone

    from jev_ultrafast.trading.data import download_market, normalize_symbol
    assert normalize_symbol(' brk.b ') == 'BRK-B'
    body = yahoo_fixture()
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.path.endswith('/AAPL')
        return httpx.Response(200, json=body)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = download_market('aapl', client, now=datetime(2026, 2, 3, 18, tzinfo=timezone.utc))
    assert result.symbol == 'AAPL'
    assert result.source == 'yahoo-finance'
    assert result.bars[-1].date == '2026-02-02'
    assert result.metadata['symbol'] == 'AAPL'
    assert result.metadata['currency'] == 'USD'
    assert result.metadata['dataset_digest'] == result.digest
    assert len(calls) == 1


@pytest.mark.parametrize('bad', ['symbol', 'currency', 'market', 'empty', 'short', 'length'])
def test_yahoo_rejects_wrong_symbol_market_or_incomplete_response(bad):
    from jev_ultrafast.trading.data import download_market
    body = yahoo_fixture()
    result = body['chart']['result'][0]
    if bad == 'symbol':
        result['meta']['symbol'] = 'MSFT'
    elif bad == 'currency':
        result['meta']['currency'] = 'EUR'
    elif bad == 'market':
        result['meta']['exchangeTimezoneName'] = 'Europe/London'
    elif bad == 'empty':
        body['chart']['result'] = None
    elif bad == 'short':
        result['indicators']['quote'][0]['close'] = [None] * 22
    else:
        result['indicators']['quote'][0]['volume'] = [1]
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        with pytest.raises(ValueError):
            download_market('AAPL', client)


@pytest.mark.parametrize('status', [404, 429, 503])
def test_market_http_failure_does_not_retry(status):
    from jev_ultrafast.trading.data import download_market
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, text='provider error')
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError):
            download_market('AAPL', client)
    assert len(calls) == 1
