"""Render entry point: Caddy authenticates HTTPS visitors; Python stays on loopback."""

import os
import re
import signal
import subprocess
import threading
from urllib.parse import urlsplit

from .server import create_server


def cloud_settings(env):
    origin = env.get('TRADING_PUBLIC_ORIGIN') or env.get('RENDER_EXTERNAL_URL', '')
    parsed = urlsplit(origin)
    if (not re.fullmatch(r'https://[a-zA-Z0-9.-]+', origin) or not parsed.hostname
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Set TRADING_PUBLIC_ORIGIN or RENDER_EXTERNAL_URL to the HTTPS site origin.')
    password = env.get('TRADING_LOGIN_PASSWORD', '')
    if not 24 <= len(password.encode('utf-8')) <= 72 or any(ord(char) < 32 for char in password):
        raise ValueError('TRADING_LOGIN_PASSWORD must contain 24-72 UTF-8 bytes without control characters.')
    port_text = env.get('PORT', '10000')
    if not port_text.isascii() or not port_text.isdecimal():
        raise ValueError('PORT must be an integer.')
    port = int(port_text)
    if not 1024 <= port <= 65535 or port == 8767:
        raise ValueError('PORT must be 1024-65535, excluding the internal port 8767.')
    return dict(origin=origin, hostname=parsed.hostname, port=port, password=password,
                root=env.get('TRADING_DATA_DIR', '/app/artifacts/trading'))


def proxy_config(settings, password_hash):
    if not re.fullmatch(r'\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}', password_hash):
        raise ValueError('Password hashing failed.')
    # Validate every value interpolated into Caddy's syntax before use.
    return f"""{{
    admin off
    auto_https off
}}
:{settings['port']} {{
    route {{
    @wrong_host {{
        not host {settings['hostname']}
    }}
    respond @wrong_host 403
    @wrong_origin {{
        method POST
        not header Origin {settings['origin']}
    }}
    respond @wrong_origin 403
    handle /healthz {{
        reverse_proxy 127.0.0.1:8767 {{
            header_up Host 127.0.0.1:8767
            header_up -Authorization
        }}
    }}
    handle {{
        basic_auth {{
            owner {password_hash}
        }}
        reverse_proxy 127.0.0.1:8767 {{
            header_up Host 127.0.0.1:8767
            header_up Origin http://127.0.0.1:8767
            header_up -Authorization
        }}
    }}
    }}
}}
"""


def main():
    settings = cloud_settings(os.environ)
    child_env = {key: value for key, value in os.environ.items()
                 if key not in ('TRADING_LOGIN_PASSWORD', 'TYPESAFE_API_KEY', 'ALPHAVANTAGE_API_KEY',
                                'TEXT_MODEL_API_KEY')}
    # Password travels via stdin, never command-line arguments or a config file.
    hashed = subprocess.run(['caddy', 'hash-password'], input=(settings.pop('password') + '\n').encode('utf-8'),
                            capture_output=True, check=True, timeout=30, env=child_env).stdout.decode().strip()
    config = proxy_config(settings, hashed)
    server = create_server(root=settings['root'])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = None
    try:
        proxy = subprocess.Popen(['caddy', 'run', '--config', '-', '--adapter', 'caddyfile'],
                                 stdin=subprocess.PIPE, text=True, env=child_env)
        # Render sends SIGTERM; stop the proxy and close the Python listener cleanly.
        def stop(*_):
            proxy.terminate()
        signal.signal(signal.SIGTERM, stop)
        proxy.stdin.write(config)
        proxy.stdin.close()
        code = proxy.wait()
        if code:
            raise SystemExit(code)
    finally:
        if proxy is not None and proxy.poll() is None:
            proxy.terminate()
            try:
                proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
