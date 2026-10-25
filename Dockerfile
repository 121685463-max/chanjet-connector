FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CHANJET_DB_PATH=/data/chanjet.db

WORKDIR /app

COPY requirements.txt .
# 使用火山引擎内网 PyPI 镜像（火山广州服务器直连 pypi.org 慢）
RUN pip install --no-cache-dir \
    -i https://mirrors.ivolces.com/pypi/simple/ \
    --trusted-host mirrors.ivolces.com \
    -r requirements.txt

COPY app ./app

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
