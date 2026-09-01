FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir aiohttp>=3.9.0

COPY backends/ ./backends/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "backends.server"]
