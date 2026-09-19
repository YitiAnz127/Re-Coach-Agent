FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装依赖：优先用锁文件保证构建可复现（requirements.lock.txt 是完整快照），
# 锁文件缺失时退回 requirements.txt。dev 依赖（pytest）不进生产镜像。
COPY requirements.txt requirements.lock.txt ./
RUN if [ -f requirements.lock.txt ]; then \
        grep -v -i '^pytest' requirements.lock.txt > /tmp/prod.lock; \
        pip install --no-cache-dir -r /tmp/prod.lock; \
    else \
        grep -v -i '^pytest' requirements.txt > /tmp/prod.txt; \
        pip install --no-cache-dir -r /tmp/prod.txt; \
    fi \
    && rm -f /tmp/prod.lock /tmp/prod.txt

# 复制应用代码
COPY . .

# 以非 root 运行：应用被攻破时不等于宿主机 root，且不拥有挂载目录
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 recoach \
    && mkdir -p /app/data \
    && chown -R recoach:recoach /app

USER recoach

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# 启动应用（host 由 compose 的端口绑定决定，容器内仍需监听全部接口）。
# --limit-concurrency 限制同时在处理的连接数，与 app 层的并发 Turn 闸门互补。
# 请求体大小上限由 app 层 MaxBodySizeMiddleware 负责（uvicorn 无此 CLI 选项）。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "128"]
