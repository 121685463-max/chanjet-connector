# 畅捷通易代账连接器 - 部署脚本
# 用法: 在服务器上执行 ./deploy.sh
set -euo pipefail

IMAGE=chanjet-connector
CONTAINER=chanjet-connector
PORT=8000

# 读取 .env（若存在）
ENV_ARGS=()
if [ -f .env ]; then
  while IFS= read -r line; do
    case "$line" in
      ""|\#*) continue ;;
      *) ENV_ARGS+=(-e "$line") ;;
    esac
  done < .env
fi

echo "==> 构建镜像"
docker build -t "$IMAGE" .

echo "==> 停止并移除旧容器"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

echo "==> 启动容器"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "127.0.0.1:${PORT}:8000" \
  -v chanjet_data:/data \
  "${ENV_ARGS[@]}" \
  "$IMAGE"

echo "==> 等待启动"
sleep 2
docker logs --tail 20 "$CONTAINER" || true
echo "==> 完成"
