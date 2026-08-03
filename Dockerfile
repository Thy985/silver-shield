FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# OpenCV 需要系统级图形/编解码库。
# 使用阿里云 Debian 镜像源加速：ECS 环境下 deb.debian.org 极慢（曾卡 50+ 分钟）。
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null || true; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 先拷贝构建元数据与源码：requirements.txt 是 `-e .[demo]`，editable install
# 需要 pyproject.toml 与 src 就位，因此必须在 pip install 之前 COPY。
COPY pyproject.toml .
COPY requirements.txt .
COPY src ./src
COPY config ./config
COPY scripts ./scripts

# 使用阿里云 PyPI 镜像源加速重依赖（torch / ultralytics / opencv-python）安装。
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

CMD ["python", "scripts/run.py"]
