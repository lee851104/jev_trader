FROM caddy:2.11.4 AS proxy
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=proxy /usr/bin/caddy /usr/local/bin/caddy
# Render forbids file capabilities; port 10000 needs no privileged-port capability.
RUN python -c "import os; p='/usr/local/bin/caddy'; a='security.capability'; os.removexattr(p,a) if a in os.listxattr(p) else None"
COPY pyproject.toml README.md ./
COPY jev_ultrafast ./jev_ultrafast
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 trader && chown -R trader:trader /app
USER trader
ENV PORT=10000 TRADING_DATA_DIR=/app/artifacts/trading
EXPOSE 10000
CMD ["python", "-m", "jev_ultrafast.trading.cloud"]
