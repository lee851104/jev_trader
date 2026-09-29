import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

from jev_ultrafast.trading.cloud import cloud_settings, proxy_config
from jev_ultrafast.trading.server import create_server


@pytest.mark.skipif(not os.environ.get('CADDY_BINARY'), reason='Optional real Caddy deployment check')
def test_real_proxy_guards():
    binary = str(Path(os.environ['CADDY_BINARY']).resolve())
    settings = cloud_settings({'RENDER_EXTERNAL_URL': 'https://paper.onrender.com', 'PORT': '18767',
                               'TRADING_LOGIN_PASSWORD': 'test-password-used-only-in-local-validation'})
    hashed = subprocess.run([binary, 'hash-password'], input=(settings['password'] + '\n').encode('utf-8'),
                            capture_output=True, check=True).stdout.decode().strip()
    with tempfile.TemporaryDirectory() as directory:
        server = create_server(port=0, root=directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        config = proxy_config(settings, hashed).replace('127.0.0.1:8767', f'127.0.0.1:{server.server_port}')
        proxy = subprocess.Popen([binary, 'run', '--config', '-', '--adapter', 'caddyfile'],
                                 stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 text=True)
        try:
            proxy.stdin.write(config)
            proxy.stdin.close()
            with httpx.Client(base_url='http://127.0.0.1:18767', trust_env=False, timeout=15,
                              headers={'Host': settings['hostname']}) as client:
                for _ in range(50):
                    try:
                        health = client.get('/healthz')
                        break
                    except httpx.ConnectError:
                        time.sleep(0.1)
                assert health.status_code == 200 and health.json() == {'status': 'ok'}
                assert client.get('/').status_code == 401
                assert client.get('/api/state').status_code == 401
                assert client.get('/api/runs').status_code == 401
                assert client.get('/api/export?id=invalid').status_code == 401
                assert client.get('/healthz', headers={'Host': 'evil.invalid'}).status_code == 403
                auth = ('owner', settings['password'])
                assert client.get('/api/state', auth=('owner', 'wrong')).status_code == 401
                page = client.get('/', auth=auth)
                print('Authorized page status:', page.status_code)
                assert page.status_code == 200
                headers = {'Origin': settings['origin'], 'X-CSRF-Token': server.app.token}
                body = {'source': 'demo', 'mode': 'rule'}
                assert client.post('/api/start', headers=headers, json=body).status_code == 401
                assert client.post('/api/start', auth=auth, json=body).status_code == 403
                assert client.post('/api/start', auth=auth, json=body,
                                   headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
                assert client.post('/api/start', auth=auth, json=body,
                                   headers={'Origin': settings['origin']}).status_code == 403
                assert client.post('/api/start', auth=auth, json=body,
                                   headers={**headers, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
                response = client.post('/api/start', auth=auth, headers=headers, json=body)
                assert response.status_code == 200, response.text
                assert response.json()['session']['model_calls'] == 0
                print('PASS: real Caddy login, health, Host/Origin/CSRF guards and offline request. No paid API calls.')
        finally:
            proxy.terminate()
            proxy.wait(timeout=15)
            server.shutdown()
            server.server_close()
            thread.join()
