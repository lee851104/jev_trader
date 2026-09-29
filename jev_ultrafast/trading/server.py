"""Loopback-only paper trading server. Run with `uv run jev-trader`."""

import json
import os
import secrets
import threading
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from .data import demo_dataset, download_alpha, download_market, normalize_symbol, parse_csv, spy_sample
from .decision import JevDecider, RuleDecider
from .engine import Costs
from .session import Conflict, Session
from .storage import Store

STATIC = Path(__file__).parent / 'static'
MAX_BODY = 2 * 1024 * 1024


def load_environment():
    path = Path.cwd() / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, value = line.split('=', 1)
            os.environ.setdefault(name.strip(), value.strip().strip('"\''))


class Application:
    def __init__(self, root):
        self.store = Store(root)
        self.token = secrets.token_urlsafe(32)
        self.session = None
        self.lock = threading.RLock()

    def state(self):
        return dict(session=self.session.snapshot() if self.session else None,
                    configured=dict(jev=bool(os.environ.get('TYPESAFE_API_KEY', '').strip()),
                                    alpha=bool(os.environ.get('ALPHAVANTAGE_API_KEY', '').strip())),
                    cached=bool(self.store.cached_symbols()), cached_symbols=self.store.cached_symbols())

    def command(self, name, body):
        with self.lock:
            if name == 'start':
                mode, source = body.get('mode'), body.get('source')
                symbol = normalize_symbol(body.get('symbol', 'SPY'))
                start_at = body.get('start_at', 'replay')
                if start_at not in ('latest', 'replay'):
                    raise ValueError('請選擇最新行情判斷或歷史重播。')
                if mode not in ('rule', 'jev') or source not in ('demo', 'sample', 'yahoo', 'alpha', 'cache', 'csv'):
                    raise ValueError('請選擇有效的資料與決策模式。')
                if source in ('demo', 'sample') and symbol != 'SPY':
                    raise ValueError('內建樣本僅支援 SPY；其他代號請選擇下載行情。')
                if source == 'demo' and mode == 'jev':
                    raise ValueError('合成示例僅供離線展示；Jev 模式請載入真實行情。')
                try:
                    costs = Costs(Decimal(str(body.get('fee', '1'))), Decimal(str(body.get('slippage_bps', '1'))))
                except InvalidOperation:
                    raise ValueError('費用與滑價必須是有效數字。') from None
                decider = (JevDecider(os.environ.get('TYPESAFE_API_KEY', ''),
                                      os.environ.get('TYPESAFE_MODEL', 'jev-latest'))
                           if mode == 'jev' else RuleDecider())
                if source == 'demo':
                    dataset = demo_dataset()
                elif source == 'sample':
                    dataset = spy_sample()
                elif source == 'csv':
                    content = body.get('csv_text', '')
                    if not isinstance(content, str):
                        raise ValueError('CSV 必須為文字。')
                    dataset = parse_csv(content, symbol)
                elif source == 'cache':
                    dataset = self.store.load_market(symbol)
                else:
                    with httpx.Client(trust_env=False) as client:
                        dataset = (download_market(symbol, client) if source == 'yahoo'
                                   else download_alpha(symbol, os.environ.get('ALPHAVANTAGE_API_KEY', ''), client))
                    if dataset.symbol != symbol:
                        raise ValueError('下載的股票代號不一致，未替換目前模擬。')
                    self.store.cache_market(dataset)
                self.session = Session(dataset, decider, costs, self.store, start_at=start_at)
            else:
                if not self.session or body.get('run_id') != self.session.state['run_id']:
                    raise Conflict('模擬帳戶已變更，請重新整理。')
                if name == 'decide':
                    self.session.decide(body.get('expected_version'))
                elif name == 'advance':
                    self.session.advance(body.get('expected_version'))
                else:
                    raise ValueError('操作不存在。')
            return self.state()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        # Query strings and malformed request bodies may contain secrets.
        pass

    def send(self, status, content, mime='application/json; charset=utf-8', filename=None):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False, allow_nan=False)
        if isinstance(content, str):
            content = content.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; "
                         "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def allowed(self, mutation=False):
        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return False
        origin = self.headers.get('Origin')
        if mutation and origin != f'http://127.0.0.1:{self.server.server_port}':
            return False
        return not mutation or secrets.compare_digest(self.headers.get('X-CSRF-Token', ''), self.server.app.token)

    def do_GET(self):
        if not self.allowed():
            return self.send(403, dict(error='禁止存取。'))
        parsed = urlparse(self.path)
        app = self.server.app
        try:
            if parsed.path == '/healthz':
                return self.send(200, dict(status='ok'))
            if parsed.path == '/api/state':
                return self.send(200, app.state())
            if parsed.path == '/api/runs':
                return self.send(200, dict(runs=app.store.list_runs()))
            if parsed.path == '/api/export':
                params = parse_qs(parsed.query)
                run_id = params.get('id', [''])[0]
                fmt = params.get('format', ['json'])[0]
                if fmt == 'csv':
                    return self.send(200, app.store.export_csv(run_id), 'text/csv; charset=utf-8', f'{run_id}.csv')
                if fmt != 'json':
                    raise ValueError('匯出格式無效。')
                return self.send(200, app.store.read(run_id), filename=f'{run_id}.json')
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/replay.js': ('replay.js', 'text/javascript; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if parsed.path not in files:
                return self.send(404, dict(error='找不到頁面。'))
            name, mime = files[parsed.path]
            content = (STATIC / name).read_text(encoding='utf-8')
            if name == 'index.html':
                content = content.replace('__CSRF_TOKEN__', app.token)
            return self.send(200, content, mime)
        except ValueError as error:
            return self.send(400, dict(error=str(error)))
        except OSError:
            return self.send(500, dict(error='檔案讀取失敗。'))

    def do_POST(self):
        if not self.allowed(mutation=True):
            self.close_connection = True
            return self.send(403, dict(error='禁止存取，請從本機頁面操作。'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length > MAX_BODY:
                self.close_connection = True
                # Drain a bounded upload before closing: Windows otherwise resets the
                # socket while the client is still sending, hiding the 413 response.
                self.connection.settimeout(2)
                remaining = min(length, MAX_BODY * 2)
                try:
                    while remaining:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                except OSError:
                    pass
                return self.send(413, dict(error='資料不得超過 2 MiB。'))
            if length <= 0:
                raise ValueError('請提供 JSON 內容。')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('請提供 JSON 物件。')
            path = urlparse(self.path).path
            if path not in ('/api/start', '/api/decide', '/api/advance'):
                return self.send(404, dict(error='操作不存在。'))
            return self.send(200, self.server.app.command(path.removeprefix('/api/'), body))
        except Conflict as error:
            return self.send(409, dict(error=str(error)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return self.send(400, dict(error='JSON 格式不正確。'))
        except ValueError as error:
            return self.send(400, dict(error=str(error)))
        except (OSError, TypeError):
            return self.send(500, dict(error='操作失敗，請重新整理並檢查本機紀錄。'))


def create_server(host='127.0.0.1', port=8767, root=None):
    if host != '127.0.0.1':
        raise ValueError('僅允許本機服務。')
    server = ThreadingHTTPServer((host, port), Handler)
    server.app = Application(Path(root) if root else Path.cwd() / 'artifacts' / 'trading')
    return server


def main():
    load_environment()
    server = create_server(port=int(os.environ.get('TRADING_PORT', '8767')))
    print(f'美股模擬交易：http://127.0.0.1:{server.server_port}（Ctrl+C 停止）')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
