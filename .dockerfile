# 与 Dockerfile 内容一致；构建示例: docker build -f .dockerfile -t vc-agent-scheduler .

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

VOLUME ["/app/data", "/app/output"]

ENTRYPOINT ["./docker-entrypoint.sh"]
