# VC Agent：7×24 调度（APScheduler）+ YouTube/RSS 流水线
# 构建: docker build -t vc-agent-scheduler .
# 运行: docker run --env-file .env -v "$(pwd)/data:/app/data" vc-agent-scheduler

FROM python:3.11-slim-bookworm

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh

# 持久化 SQLite / 输出 / 偏好：挂载 -v ./data:/app/data
VOLUME ["/app/data", "/app/output"]

ENTRYPOINT ["./docker-entrypoint.sh"]
