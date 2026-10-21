# M2c — 本地执行可靠性基线

本版本延续 M1/M2b 的单机模式，不是生产级 exactly-once 系统，也不应暴露公网。

## 解决什么问题？

**场景 A：同一个海报请求被重发。** 浏览器网络超时后再次点击，可能再次调用收费图像模型。现在浏览器发送 `Idempotency-Key`，服务端在收费模型调用前写入持久化执行账本。

**场景 B：模型调用后连接丢失，服务端不确定是否计费。** 这类结果不能通过简单的 retry 解决。相同键会返回 409，并保留 `UNKNOWN` 记录，避免无限重试。

**场景 C：服务意外退出。** 重启时，上一进程遗留的 `RUNNING` 执行会转为 `UNKNOWN`，不会自动再次调用模型。

**场景 D：Qwen Brief 编译返回限流错误。** 只针对明确的 HTTP 429 进行最多一次额外尝试；不自动重试模糊的超时、503 或图像生成调用。

## 新增 API

- `POST /api/posters`：可选 HTTP 请求头 `Idempotency-Key: <唯一ID>`，客户端页面默认传入随机 UUID。
- `GET /api/executions/{key}`：查询执行状态与事件。仅保存状态与事件名，不记录图片、业务 Prompt 或 API Key。

执行状态：

| 状态 | 含义 |
|---|---|
| `RUNNING` | 当前进程已经声明此键的执行权；任务可能正在运行 |
| `COMPLETED` | 结果已保存，可复用原 poster_id / 版本 |
| `UNKNOWN` | 执行失败或结果丢失，可能已经产生计费；需人工排查 |

该账本通过 SQLite `BEGIN IMMEDIATE` 和主键约束防止同一单机数据库内的重复 claim。重复同一键和同一输入，只有 `COMPLETED` 才复用原结果；改变价格等输入会拒绝复用。

浏览器会为同一组表单值和上传文件元数据保留同一个键，并显示最近一次执行状态。用户确实要产生新的创意方案时，应该显式创建新的请求，而不是误把网络重试当成重新生成。

## 本地验证

```bash
cd ~/repos/poster-agent
git pull --ff-only origin main
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

上传示例商品图，用本地背景生成一次。保持表单与图片不变，再点击“生成海报”；必须得到同一 `poster_id`。点击“查询最近执行状态”，应看到 `COMPLETED` 以及 `CLAIMED`、`COMPLETED` 事件。

若需要检查正在执行的 Key：

```bash
curl http://127.0.0.1:8000/api/executions/你的执行Key
```

数据库默认：`data/executions.sqlite3`（已列入 gitignore）；可以在 `.env` 中设置 `POSTER_EXECUTION_DB`。备份和恢复需要用户自行进行。

## 仍然没有解决的问题

1. 没有用户登录、授权：随机 Idempotency-Key 是请求定位符，不是安全凭证，**不能部署公网**。
2. 无跨数据库事务：文件保存成功但账本写入失败时，可能出现 `UNKNOWN` 与孤立文件，不保证绝对恰好一次。
3. 没有超时任务人工对账/恢复 UI，也没有可靠异步队列；图片 API 仍由请求进程同步等待。
4. 图像模型的返回 URL、云端任务 ID 等未建立可对账的 Provider Receipt；不能保证自动确认已产生的费用。
5. 单机模式下，服务启动时执行遗留任务回收；不要启用多 Worker。
6. 接受 `Idempotency-Key` 为可选是为了兼容 M1 手动接口，不传键仍可能重复生图。正式版本应强制携带。
7. 输入素材、业务数据和审查 ID 未添加多租户隔离，不能作为企业生产级版本。

下一步不应继续堆基础设施：应在真实用户操作中验证 M1/M2，随后启动 M3 的 OCR + 确定性品牌规则检查。
