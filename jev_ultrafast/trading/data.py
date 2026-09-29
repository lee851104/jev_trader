"""Validated daily bars. No market credentials leave this module."""

import csv
import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

FIELDS = ('date', 'open', 'high', 'low', 'close', 'volume')


@dataclass(frozen=True)
class Bar:
    date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int

    def as_dict(self):
        return {key: str(getattr(self, key)) if key != 'volume' else self.volume for key in FIELDS}


@dataclass(frozen=True)
class Dataset:
    bars: tuple[Bar, ...]
    source: str
    digest: str
    metadata: dict | None = None
    symbol: str = 'SPY'


def spy_sample() -> Dataset:
    folder = Path(__file__).parent / 'samples'
    dataset = parse_csv((folder / 'spy.csv').read_text(encoding='utf-8'))
    metadata = json.loads((folder / 'spy-source.json').read_text(encoding='utf-8'))
    if metadata['symbol'] != 'SPY' or metadata['dataset_digest'] != dataset.digest:
        raise ValueError('SPY 樣本來源與資料不一致。')
    return Dataset(dataset.bars, 'yahoo-snapshot', dataset.digest, metadata)


def normalize_symbol(symbol):
    if not isinstance(symbol, str):
        raise ValueError('請輸入有效的美股代號，例如 AAPL、MSFT 或 SPY。')
    symbol = symbol.strip().upper().replace('.', '-')
    if not re.fullmatch(r'[A-Z][A-Z0-9]{0,9}(?:-[A-Z0-9]{1,3})?', symbol):
        raise ValueError('請輸入有效的美股代號，例如 AAPL、MSFT 或 SPY。')
    return symbol


def _dataset(rows, source, symbol='SPY'):
    bars = []
    try:
        for row in rows:
            day = row['date']
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError()
            values = [Decimal(str(row[k])) for k in FIELDS[1:5]]
            if any(not p.is_finite() or not 0 < p <= 1000000 for p in values):
                raise ValueError()
            opening, high, low, close = values
            if not low <= min(opening, close) <= max(opening, close) <= high:
                raise ValueError()
            volume = str(row['volume'])
            if not volume.isascii() or not volume.isdigit() or int(volume) > 10**15:
                raise ValueError()
            if bars and day <= bars[-1].date:
                raise ValueError()
            bars.append(Bar(day, *values, int(volume)))
        if not 21 <= len(bars) <= 10000:
            raise ValueError()
    except (ValueError, KeyError, TypeError, InvalidOperation):
        raise ValueError('行情須為 21–10,000 筆依日期遞增且不重複的有效 OHLCV 資料。') from None
    canonical = json.dumps([b.as_dict() for b in bars], sort_keys=True)
    return Dataset(tuple(bars), source, hashlib.sha256(canonical.encode()).hexdigest(), symbol=normalize_symbol(symbol))


def parse_csv(text: str, symbol='SPY') -> Dataset:
    if len(text.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('CSV 不得超過 2 MiB。')
    try:
        reader = csv.DictReader(io.StringIO(text.lstrip('\ufeff')), strict=True)
        if reader.fieldnames != list(FIELDS):
            raise ValueError('CSV 欄位須為 date,open,high,low,close,volume。')
        rows = list(reader)
    except csv.Error:
        raise ValueError('CSV 格式無效或單一欄位過長。') from None
    if any(None in row for row in rows):
        raise ValueError('CSV 欄位數不正確。')
    return _dataset(rows, 'csv', symbol)


def to_csv(dataset: Dataset) -> str:
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(b.as_dict() for b in dataset.bars)
    return output.getvalue()


def demo_dataset() -> Dataset:
    rows = []
    day = date(2026, 1, 5)
    while len(rows) < 60:
        if day.weekday() < 5:
            n = len(rows)
            opening = Decimal(str(round(500 + n * .18 + 8 * math.sin(n / 3), 2)))
            close = opening + Decimal(str(round(2 * math.cos(n / 3), 2)))
            rows.append(dict(date=day.isoformat(), open=str(opening), high=str(max(opening, close) + 2),
                             low=str(min(opening, close) - 2), close=str(close), volume=50000000 + n * 10000))
        day += timedelta(days=1)
    return _dataset(rows, 'synthetic')


def download_spy(key: str, client: httpx.Client) -> Dataset:
    return download_alpha('SPY', key, client)


def download_alpha(symbol: str, key: str, client: httpx.Client) -> Dataset:
    symbol = normalize_symbol(symbol)
    if not key.strip():
        raise ValueError('請在伺服器 .env 設定 ALPHAVANTAGE_API_KEY。')
    try:
        response = client.get('https://www.alphavantage.co/query', params={
            'function': 'TIME_SERIES_DAILY', 'symbol': symbol.replace('-', '.'), 'outputsize': 'compact', 'apikey': key,
        }, timeout=25)
        response.raise_for_status()
        body = response.json()
        if normalize_symbol(body['Meta Data']['2. Symbol']) != symbol:
            raise ValueError()
        series = body['Time Series (Daily)']
        rows = [dict(date=day, **{name: values[f'{i}. {name}'] for i, name in enumerate(FIELDS[1:], 1)})
                for day, values in sorted(series.items())]
        return _dataset(rows, 'alpha-vantage', symbol)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError('行情下載失敗或額度已用完，請稍後手動重試或匯入 CSV。') from None



def download_market(symbol: str, client: httpx.Client, *, now=None) -> Dataset:
    """Download USD US equity/ETF daily bars, conservatively excluding today's UTC bar."""
    symbol = normalize_symbol(symbol)
    now = now or datetime.now(timezone.utc)
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    try:
        response = client.get(url, params={'interval': '1d', 'range': '3mo'},
                              headers={'User-Agent': 'Mozilla/5.0'}, timeout=25)
        response.raise_for_status()
        chart = response.json()['chart']
        if chart.get('error'):
            raise ValueError()
        result = chart['result'][0]
        meta = result['meta']
        if (normalize_symbol(meta['symbol']) != symbol or meta['currency'] != 'USD'
                or meta['instrumentType'] not in ('EQUITY', 'ETF')
                or meta['exchangeTimezoneName'] != 'America/New_York'):
            raise ValueError()
        timestamps = result['timestamp']
        quotes = result['indicators']['quote'][0]
        if any(len(quotes[field]) != len(timestamps) for field in FIELDS[1:]):
            raise ValueError()
        rows = []
        for i, timestamp in enumerate(timestamps):
            day = datetime.fromtimestamp(timestamp, timezone.utc).date()
            if day >= now.astimezone(timezone.utc).date():
                continue
            values = {field: quotes[field][i] for field in FIELDS[1:]}
            if any(value is None for value in values.values()):
                continue
            rows.append(dict(date=day.isoformat(), **values))
        dataset = _dataset(rows, 'yahoo-finance', symbol)
        metadata = dict(symbol=symbol, currency='USD', provider='Yahoo Finance',
                        source_url=str(response.request.url), retrieved_at=now.isoformat(),
                        first_date=dataset.bars[0].date, last_date=dataset.bars[-1].date,
                        rows=len(dataset.bars), dataset_digest=dataset.digest,
                        price_basis='quote OHLC; not adjclose; excludes download UTC day; no dividend simulation')
        return Dataset(dataset.bars, dataset.source, dataset.digest, metadata, symbol)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError, OSError):
        raise ValueError(f'{symbol} 行情下載失敗：代號不存在、來源暫時不可用，或不是支援的美元美股／ETF。'
                         '請確認代號或改用已下載快取／CSV；未替換目前模擬。') from None
