FROM python:3.12-slim

WORKDIR /app

# 系統依賴（psycopg2 需要 libpq）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先複製 requirements 以利 layer cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用程式碼
COPY agent/ ./agent/
COPY api/ ./api/
COPY config.py main.py ./

# audit log 目錄
RUN mkdir -p /app/logs

EXPOSE 9090

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9090"]
