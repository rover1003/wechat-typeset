FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp/wechat_typeset_uploads /tmp/wechat_token_cache

EXPOSE 9120

CMD ["gunicorn", "--bind", "0.0.0.0:9120", "--workers", "2", "--timeout", "120", "app:app"]
