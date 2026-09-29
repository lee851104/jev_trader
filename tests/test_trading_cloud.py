"""Deployment guards; no model or market network calls."""
import pytest

from jev_ultrafast.trading.cloud import cloud_settings, proxy_config


def settings(**extra):
    return cloud_settings(dict(RENDER_EXTERNAL_URL='https://paper.onrender.com',
                               TRADING_LOGIN_PASSWORD='a-long-test-password-123456789', **extra))


def test_cloud_requires_login_and_https_origin():
    for env in [{}, {'RENDER_EXTERNAL_URL': 'https://paper.onrender.com'},
                {'RENDER_EXTERNAL_URL': 'http://paper.onrender.com', 'TRADING_LOGIN_PASSWORD': 'x' * 32},
                {'RENDER_EXTERNAL_URL': 'https://paper.onrender.com/path', 'TRADING_LOGIN_PASSWORD': 'x' * 32},
                {'RENDER_EXTERNAL_URL': 'https://user:pass@paper.onrender.com',
                 'TRADING_LOGIN_PASSWORD': 'x' * 32}]:
        with pytest.raises(ValueError):
            cloud_settings(env)


@pytest.mark.parametrize('port', ['0', '65536', 'abc', '8767', '10000\nrespond 200'])
def test_cloud_rejects_invalid_or_backend_port(port):
    with pytest.raises(ValueError):
        settings(PORT=port)


def test_proxy_protects_app_and_preserves_origin_guard():
    config = proxy_config(settings(), '$2a$14$' + 'a' * 53)
    assert 'route {' in config  # Keep Host/Origin rejection before health/auth handlers.
    assert 'admin off' in config
    assert 'not host paper.onrender.com' in config
    assert 'not header Origin https://paper.onrender.com' in config
    assert 'basic_auth' in config
    assert 'owner $2a$14$' in config
    assert 'header_up -Authorization' in config
    assert 'header_up Host 127.0.0.1:8767' in config
    assert 'header_up Origin http://127.0.0.1:8767' in config
    assert 'a-long-test-password' not in config
    assert config.index('respond @wrong_origin 403') < config.index('handle /healthz')
    assert 'retry' not in config


def test_cloud_password_and_origin_cannot_inject_proxy_config():
    for value in ['short', 'x' * 73, 'line\n' + 'x' * 25]:
        with pytest.raises(ValueError):
            cloud_settings({'RENDER_EXTERNAL_URL': 'https://paper.onrender.com',
                            'TRADING_LOGIN_PASSWORD': value})
    for value in ['https://host;invalid', 'https://paper.onrender.com#fragment',
                  'https://paper.onrender.com?query=1', 'https://paper.onrender.com\n']:
        with pytest.raises(ValueError):
            cloud_settings({'RENDER_EXTERNAL_URL': value, 'TRADING_LOGIN_PASSWORD': 'x' * 32})
    with pytest.raises(ValueError):
        proxy_config(settings(), 'invalid hash\nrespond 200')
