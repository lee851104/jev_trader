"""Atomic local evidence files. The run id never denotes an arbitrary path."""

import csv
import io
import json
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path

from .data import normalize_symbol, parse_csv, to_csv


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id):
            raise ValueError('執行識別碼無效。')
        path = self.root / f'{run_id}.json'
        if path.is_symlink():
            raise ValueError('不允許連結檔案。')
        return path

    def write(self, run_id, state):
        path = self._path(run_id)
        text = json.dumps(state, ensure_ascii=False, allow_nan=False, indent=2)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root,
                                             suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def read(self, run_id):
        try:
            return json.loads(self._path(run_id).read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            raise ValueError('找不到可讀取的執行紀錄。') from None

    def list_runs(self):
        results = []
        for path in sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                state = self.read(path.stem)
                results.append({**{k: state[k] for k in ('run_id', 'created_at', 'source', 'mode', 'status', 'date')},
                                'symbol': state.get('symbol', 'SPY')})
            except (ValueError, KeyError, TypeError):
                continue
        return results

    def export_csv(self, run_id):
        state = self.read(run_id)
        output = io.StringIO(newline='')
        fields = ('symbol', 'decision_date', 'action', 'model', 'fill_date', 'status', 'quantity', 'price', 'fee',
                  'cash', 'shares', 'equity', 'reason')
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for event in state['history']:
            decision, fill = event['decision'], event.get('settlement', {})
            account = event.get('account', {})
            row = dict(symbol=state.get('symbol', 'SPY'), decision_date=event['date'],
                       action=decision['choice'], model=decision['model'],
                       fill_date=fill.get('date', ''), status=fill.get('status', 'pending'),
                       quantity=fill.get('quantity', 0), price=fill.get('price', ''), fee=fill.get('fee', ''),
                       cash=account.get('cash', ''), shares=account.get('shares', ''), equity=event.get('equity', ''),
                       reason=fill.get('reason', ''))
            for key, value in row.items():
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
                    row[key] = "'" + value
            writer.writerow(row)
        return '\ufeff' + output.getvalue()


    def cached_symbols(self):
        symbols = []
        for path in self.root.glob('market-*.json'):
            symbol = path.stem.removeprefix('market-')
            try:
                if not path.is_symlink() and normalize_symbol(symbol) == symbol:
                    symbols.append(symbol)
            except ValueError:
                continue
        if (self.root / 'spy-cache.csv').is_file():
            symbols.append('SPY')
        return sorted(set(symbols))

    def cache_market(self, dataset):
        symbol = normalize_symbol(dataset.symbol)
        path = self.root / f'market-{symbol}.json'
        if path.is_symlink():
            raise ValueError('不允許連結檔案。')
        value = dict(symbol=symbol, source=dataset.source, digest=dataset.digest,
                     metadata=dataset.metadata, csv=to_csv(dataset))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root,
                                             suffix='.tmp', delete=False) as stream:
                temporary = stream.name
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def load_market(self, symbol):
        symbol = normalize_symbol(symbol)
        path = self.root / f'market-{symbol}.json'
        if path.is_symlink():
            raise ValueError('不允許連結檔案。')
        legacy = self.root / 'spy-cache.csv'
        if not path.exists() and symbol == 'SPY' and legacy.is_file() and not legacy.is_symlink():
            return replace(parse_csv(legacy.read_text(encoding='utf-8')), source='alpha-cache')
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            dataset = parse_csv(payload['csv'], symbol)
            if payload['symbol'] != symbol or dataset.digest != payload['digest']:
                raise ValueError()
            metadata = payload.get('metadata') or {}
            if metadata.get('symbol', symbol) != symbol:
                raise ValueError()
            return replace(dataset, source='market-cache', metadata={**metadata, 'cached_from': payload['source']})
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError(f'{symbol} 沒有可用的行情快取，請先下載該股票行情。') from None
