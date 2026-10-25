# 畅捷通易代账连接器 (chanjet-connector)

对接用友畅捷通「易代账」开放平台，拉取客户账套财务数据（资产负债表 / 明细账），
服务于品牌小程序「看账」功能。

## 能力

- OAuth2 授权（authorization_code）+ token 刷新 + 持久化（SQLite）
- 账套列表拉取
- 资产负债表（结构化接口优先，回退传统接口）
- 明细账分页查询
- 结构化 JSON 日志、参数校验、异常归一化

## 技术栈

FastAPI + httpx + SQLite + Docker。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查（不依赖凭证） |
| GET | `/api/v1/auth/authorize/chanjet` | 跳转授权页 |
| GET | `/api/v1/auth/callback/chanjet` | 授权回调，换 token |
| GET | `/api/v1/chanjet/account-books` | 账套列表 |
| GET | `/api/v1/chanjet/balance-sheet/{book_id}?period=yyyyMM` | 资产负债表 |
| GET | `/api/v1/chanjet/detail-ledger/{book_id}?startPeriod=yyyyMM&endPeriod=yyyyMM` | 明细账 |

## 环境变量

见 `.env.example`。核心：

- `CHANJET_APP_KEY` / `CHANJET_APP_SECRET`：开放平台应用凭证（沙箱/正式同域名，靠 appKey 区分）
- `CHANJET_REDIRECT_URI`：回调地址，必须与应用配置完全一致
- `CHANJET_ORG_ID`：可选，授权时指定企业 ID

## 部署

```bash
# 服务器上
cp .env.example .env   # 填入凭证
bash deploy.sh         # 用 bash 执行，避免 git 拉取后丢失可执行位
```

容器仅监听 `127.0.0.1:8000`，由 openresty 反代 `/api/v1/auth/callback/chanjet` 与 `/api/` 前缀。

## 授权流程

1. 浏览器访问 `/api/v1/auth/authorize/chanjet` → 跳转畅捷通授权页
2. 用户授权后回调 `/api/v1/auth/callback/chanjet?code=xxx` → 换 token 落库
3. 之后调用业务接口自动使用/刷新 token
