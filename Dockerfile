FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY hamstra_telegram ./hamstra_telegram

RUN pip install --no-cache-dir .

ENTRYPOINT ["hamstra-telegram"]
