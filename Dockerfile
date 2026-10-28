FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN sed -i \
      -e 's|http://deb.debian.org/debian-security|https://mirrors.cloud.tencent.com/debian-security|g' \
      -e 's|http://deb.debian.org/debian|https://mirrors.cloud.tencent.com/debian|g' \
      -e 's|https://deb.debian.org/debian-security|https://mirrors.cloud.tencent.com/debian-security|g' \
      -e 's|https://deb.debian.org/debian|https://mirrors.cloud.tencent.com/debian|g' \
      /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY cloud_mcp.py health_analytics.py app_server.py dashboard.html ./
USER 10002:10002

CMD ["python", "app_server.py"]
