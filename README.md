# 畅捷通易代账连接器 (chanjet-connector)

对接用友畅捷通「易代账」开放平台，拉取客户账套财务数据（资产负债表 / 明细账），
服务于品牌小程序「看账」功能。

## 鉴权模式：自建应用（非商店应用）

畅捷通「自建应用」不走 OAuth 授权码回调，而是：

1. 应用管理员授权 → 拿到软证书 `certificate`
2. 平台每 10 分钟向「消息接收地址」推送 `appTicket`（AES 加密，用消息密钥解密）
3. `certificate + appTicket` 调 `POST /v1/common/auth/selfBuiltApp/generateToken` 换 `openToken`
4. 用 `appKey + appSecret + openToken` 调业务接口（账套/资产负债表/明细账）

> ⚠️ 自建应用**无沙箱环境**，直接用正式环境自测（可建 2 个自建应用分别对应测试/生产）。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查（不依赖凭证） |
| POST | `/api/v1/chanjet/message` | 接收平台推送（appTicket），AES 解密后落库 |
| POST | `/api/v1/chanjet/trigger-ticket` | 立即触发 appTicket 推送（联调辅助） |
| GET | `/api/v1/chanjet/account-books` | 账套列表 |
| GET | `/api/v1/chanjet/balance-sheet/{book_id}?period=yyyyMM` | 资产负债表 |
| GET | `/api/v1/chanjet/detail-ledger/{book_id}?startPeriod=yyyyMM&endPeriod=yyyyMM` | 明细账 |

## 环境变量

见 `.env.example`。核心：

- `CHANJET_APP_KEY` / `CHANJET_APP_SECRET`：自建应用凭证
- `CHANJET_CERTIFICATE`：软证书（应用管理员授权后获得）
- `CHANJET_MESSAGE_SECRET`：消息密钥（16 位字母数字，用于 AES 解密 appTicket）

## 部署

```bash
# 服务器上
cp .env.example .env   # 填入凭证
bash deploy.sh         # 用 bash 执行，避免 git 拉取后丢失可执行位
```

容器仅监听 `127.0.0.1:8000`，由 openresty 反代 `/api/v1/chanjet/message` 给畅捷通平台推送。

## 联调步骤

1. 阿尘在应用「权限管理」生成授权链接 → 应用管理员授权 → 拿软证书 `certificate` 交给开发
2. 阿尘在「消息订阅」配置：消息密钥（与 `.env` 的 `CHANJET_MESSAGE_SECRET` 一致）
   与消息接收地址 `http://101.96.216.161/api/v1/chanjet/message`
3. 平台推送 APP_TEST 校验地址 → 服务返回 success
4. 触发 appTicket 推送（`/api/v1/chanjet/trigger-ticket` 或等 10 分钟自动推）
5. 调账套列表/资产负债表，服务自动用 `certificate + appTicket` 换 openToken
