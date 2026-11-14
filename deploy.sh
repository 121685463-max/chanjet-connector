#!/usr/bin/env bash
# ============================================================
# 畅捷通易代账连接器 chanjet-connector 生产部署脚本（webhook 触发，在服务器执行 root）
# 铁律：代码改动一律走 GitHub（本地改→push→服务器 pull），本脚本只在服务器做 pull+build+重启。
# ============================================================
set -euo pipefail

APP_ROOT="/opt/chanjet-connector"
IMAGE=chanjet-connector
CONTAINER=chanjet-connector
# 宿主监听端口（8000 已被主后端占用，改用 8010）
PORT=8010
ENV_FILE="/etc/chanjet-connector-webhook/webhook.env"   # secret/token 只放 /etc，不落仓库

# 读取部署密钥（GITHUB_PULL_TOKEN / GIT_PROXY，只放 /etc，不落 .git/config）
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi
TOKEN="${GITHUB_PULL_TOKEN:-}"
GIT_PROXY="${GIT_PROXY:-}"

log() { echo -e "\n\033[1;36m[deploy]\033[0m $*"; }
fail() { echo -e "\033[1;31m[deploy][FAIL]\033[0m $*" >&2; exit 1; }

log "① git pull（代码来自 GitHub，走 squid 代理 + token）"
cd "$APP_ROOT"
# 服务器直连 github.com 偶发超时/断连，统一走 squid 正向代理 + 重试
MAX_PULL_ATTEMPTS="${MAX_PULL_ATTEMPTS:-5}"
PULL_RETRY_DELAY="${PULL_RETRY_DELAY:-15}"
PULL_TIMEOUT="${PULL_TIMEOUT:-90}"
if [ -n "$TOKEN" ]; then
    PULL_REMOTE="https://121685463-max:${TOKEN}@github.com/121685463-max/chanjet-connector.git"
else
    PULL_REMOTE="origin"
fi
pull_ok=0
for attempt in $(seq 1 "$MAX_PULL_ATTEMPTS"); do
    log "  git pull 第 ${attempt}/${MAX_PULL_ATTEMPTS} 次"
    if timeout "$PULL_TIMEOUT" git -c http.proxy="$GIT_PROXY" -c https.proxy="$GIT_PROXY" pull --ff-only "$PULL_REMOTE" main; then
        pull_ok=1
        break
    fi
    if [ "$attempt" -lt "$MAX_PULL_ATTEMPTS" ]; then
        log "  git pull 失败，${PULL_RETRY_DELAY}s 后重试"
        sleep "$PULL_RETRY_DELAY"
    fi
done
[ "$pull_ok" -eq 1 ] || fail "git pull 重试 ${MAX_PULL_ATTEMPTS} 次仍失败，终止部署（不 build 不重启）"

log "② 构建镜像（pip 走火山内网镜像，见 Dockerfile）"
# 读取 .env（若存在），作为环境变量注入容器
ENV_ARGS=()
if [ -f .env ]; then
  while IFS= read -r line; do
    case "$line" in
      ""|\#*) continue ;;
      *) ENV_ARGS+=(-e "$line") ;;
    esac
  done < .env
fi
docker build -t "$IMAGE" . || fail "构建镜像失败"

log "③ 停止并移除旧容器，启动新容器"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "127.0.0.1:${PORT}:8000" \
  -v chanjet_data:/data \
  "${ENV_ARGS[@]}" \
  "$IMAGE" || fail "启动容器失败"

log "④ 等待启动 + health 检查"
sleep 3
docker logs --tail 15 "$CONTAINER" || true
curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null || fail "health 检查未通过：http://127.0.0.1:${PORT}/healthz"

log "✅ 部署完成"
