# M5g — 真实商品图片离线批量导入

M5e/M5f 已具备原生 4:5 人工审核、OCR 证据与评分结果管理，但在收集首批实际商品前，每张图都要手工在浏览器创建。M5g 新增一个**单机命令行批量导入器**，优先复用现有 FastAPI `POST /api/posters`，而不是再写一套图片处理/存储方法。

**默认只有预检，不创建海报**；使用 `--apply` 才实际写入海报存储。背景强制 `local`（确定性渐变），绝不调用付费 Qwen Image API。批次图片都留在本机，不发送给外部服务。

## 1. 准备 CSV 与图片

示例目录：

```text
data/real_trial/
  real_skus.csv
  images/
    toothbrush.png
    headphones.jpg
    brand-logo.png
```

`real_skus.csv`（UTF-8，第一行为英文列名）：

```csv
sku_id,product_image,title,price,original_price,subtitle,cta,style,logo_image
sku-toothbrush,images/toothbrush.png,电动牙刷促销,199,399,限时福利,立即购买,蓝色科技风,images/brand-logo.png
sku-headphones,images/headphones.jpg,无线耳机,299,499,品质生活,立即抢购,蓝色科技风,
```

必填列：`sku_id`、`product_image`、`title`、`price`。可选：`original_price`、`subtitle`、`cta`、`style`、`logo_image`。空白的可选文案使用现有海报默认值。不可传入 `background_mode`，因为批量实验必须走本地免费背景。

- 单批 1–100 行；`sku_id` 必须唯一，且为 2–64 位 ASCII 字母、数字、`_` 或 `-`。
- 图片路径只能写**CSV 所在目录内的相对路径**；拒绝绝对路径、目录外文件、逃逸该目录的符号链接或 URL。
- 每张图片经**既有上传接口同样的预处理**：最大 10 MiB、最多 1600 万像素、单边最长 8192、最小边 64、有效 PNG/JPG/WEBP、EXIF 旋正、ICC→sRGB。
- CSV 价格和标题通过已存在的 `PosterSpec` 校验。全批先预检，任何一行有问题都会在创建第一张海报**之前**中止。
- 录入前应确保你有权使用这些商品图片；请用合法授权、已脱敏素材，不上传私人客户信息到 GitHub。

## 2. 先预检（默认、不会写海报）

```bash
cd ~/repos/poster-agent
uv sync --locked
uv run --locked python -m scripts.real_sku_batch \
  --manifest data/real_trial/real_skus.csv \
  --output-dir data/eval_runs/m5g_import
```

输出 `batch_preflight.json`：清单 SHA-256、SKU 行数、**不同原始图片哈希数**、SKU ID 与 `no_paid_model=true`。不会创建任何 poster，也不会运行 OCR。

## 3. 明确应用后创建全部海报

在**停止同一数据目录的其他写进程/服务**后执行：

```bash
uv run --locked python -m scripts.real_sku_batch \
  --manifest data/real_trial/real_skus.csv \
  --output-dir data/eval_runs/m5g_import \
  --apply
```

与普通网页生成相同：经由 FastAPI 上传/解码/结构化验证，复用本地背景、Pillow 生成 PNG，并写入原有 `POSTER_DATA_DIR`。得到的 `poster_id` 可以用网页：

```text
http://127.0.0.1:8000/api/posters/<poster_id>
http://127.0.0.1:8000/api/posters/<poster_id>/versions/1/exports/feed_4x5_native
```

完成导入会输出 `batch_import_results.json`，记录 `sku_id → poster_id`、版本号、请求 SHA、原素材 SHA 和幂等键。结果中不会保存原始图片字节，但应将这些文件视为私有运营数据，勿推到仓库。

**M5k 完整性保护：** 导入结果还记录预期的 `expected_sku_ids`、`expected_sku_count` 和 `completed`。每导入一行就保存可恢复账本，但只有全部预期 SKU 成功处理后才标记 `completed=true`。途中终止的账本可续跑、不可直接用 M5j `--score` 当作完整批次。旧版没有这三个字段的结果需要用同一 CSV、同一执行账本和同一输出目录重新执行原 `--apply` 命令，确认复用已有 ID 并补齐完成标记；**不要手动改 JSON 标记完成**。本地记录仍不是签名证明。

## 4. 失败恢复与费用防护

每个 SKU 都使用由 **CSV 绝对路径 + sku_id** 派生的稳定 `Idempotency-Key`，由现有 SQLite 执行账本保障同样请求不会重复创建。每成功一张，就原子保存本地批次清单。如果第 2/20 张失败，重新执行相同 `--apply` 能继续；已完成的 SKU 不会重复写入。若清单内容被修改、已记录的素材哈希改变、账本返回结果未知或者已成功的 `poster_id` 被手工删除，将明确报错并要求核对，**不隐式新建替代品**。

此设计仍是**单机工具而非生产 Exactly-Once 事务系统**：导入期间不要同时运行其它写进程，勿随意迁移/删除 `POSTER_EXECUTION_DB` 或编辑批次结果 JSON。即使请求返回旧的已完成结果，也必须依赖稳定的执行数据库和海报存储。

## 5. 导入后仍需真正质量验证

导入只是建立样本，不会捏造质量标签、自动给客户海报放行或运行 OCR：

1. 在浏览器逐个打开原生 4:5 海报，执行 M5c 人工质量标注；
2. 用户明确安装并执行真正的 PaddleOCR，生成 M4c 原生验收报告；
3. 用 M5e 盘点缺口：`uv run --locked python -m scripts.native_trial --reviewer local-operator`；
4. 证据齐全之后再显式 `--score`，获得 M5d 的 FP/FN/F1/弃判统计。

文件的 SHA 不等同于商品类别，20 个文件不证明具备 20 类独立 SKU；实际类别和样本代表性仍需人工记录。

## 工程验证

```bash
uv run --locked python -m pytest -q tests/test_real_sku_batch.py
```

CI 的样本来自 Pillow 人工构造的本地小图片，实际执行了 Web 创建、原有 PNG 存储和幂等账本测试，但**没有真实商业商品素材、真实 PaddleOCR 模型或 Qwen 付费调用**。

## 6. 网页批量导入与进度恢复

除了命令行流程，工作台还提供本机网页入口。打开 http://127.0.0.1:8000/ 的「本地批量商品导入与进度」区域：

1. 选择 UTF-8 CSV，并选择该 CSV 引用的全部商品图与 Logo；网页模式的 product_image / logo_image 必须是文件名，不含子目录。
2. 点击「上传并预检」。服务会校验整批的 SKU、字段、图片路径、文件类型/大小和 PosterSpec，有任意一行不合格时，不会创建任何海报。
3. 预检成功后，再显式点击「生成海报（本地背景）」。批次按 SKU 顺序执行，页面显示已完成数量、当前 SKU 和每行海报 ID；每完成一行即写入与 M5g CLI 相同格式的 batch_import_results.json。
4. 浏览器刷新或服务重启后，页面会从本机批次目录恢复状态。失败/中断批次可点击「继续批次」；重复 SKU 复用已有账本与幂等键，不会重新创建已完成海报。

网页上传使用现有上传解码与 PosterSpec 验证，并强制 background_mode=local。每张图片不超过 10 MiB，单批总上传不超过 100 MiB，最多 100 个 SKU。网页批次和图片保存在 POSTER_BATCH_DIR 指定目录；默认是被 Git 忽略的 data/eval_runs/web_batches/。这些文件包含本地运营素材，请勿提交到 GitHub。生成完成后，批次账本可通过 M5j 的 --batch-journal 参数用于批次范围盘点；它仍不代表 OCR 或人工质量审核已经完成。

该入口仍是单机、单进程 Demo：一次只允许一个网页批次执行，不提供登录、用户隔离或多进程任务队列。不要通过反向代理或共享网络开放此服务。

## 7. 查看命令行批次进度

工作台的「命令行批次账本状态（M5g）」区域只读默认账本
`data/eval_runs/m5g_import/batch_import_results.json`，显示预期、已完成和待处理 SKU 数量。它与网页批次导入区分开来：网页创建的批次保存在 `POSTER_BATCH_DIR`，在网页批次区恢复；CLI 自定义 `--output-dir` 写出的账本不会出现在默认 CLI 状态区。

状态接口为 `GET /api/cli-batches/status`，与网页批次的 `GET /api/batches/{batch_id}` 使用不同路径，避免静态路径被批次 ID 路由截获。接口只返回摘要，不返回图片、请求摘要或文件系统路径；损坏或结构不一致的账本返回 422，不会被误报为完成。



## 8. 网页批次直接进入人工审核

完整生成后，点击网页批次区域的「审核当前批次」。页面只读调用
`GET /api/batches/{batch_id}/review-scope`，核对批次状态、CSV 摘要、预期 SKU、全部导入记录和海报 ID 后，将 SKU 范围交给现有 M5h 人工验收队列，避免重新寻找、下载和选择 `batch_import_results.json`。

- 只有已完成且账本一致的网页批次才能进入整批审核；中断、缺行、重复 poster ID、清单摘要不一致时返回 409。
- 该操作仅更改当前浏览器的审核队列筛选，不修改图片、不会自动调用 OCR/模型，也不提交任何审核结果。
- 在审核区域点击「清除批次筛选」可恢复全部本地海报。原有手动选择 M5g 账本的功能保持可用，适用于 CLI 批次和自定义目录。
- 同一商品的新版本仍由验收队列显示当前最新版本；新版本不能继承旧版本的人工验收结论。

## 9. 查看和切换历史网页批次

网页「历史网页批次」提供按状态筛选、上一页/下一页和打开旧批次的入口；无需依赖浏览器记住的最后一个批次 ID。接口为 GET /api/batches?limit=10&offset=0&status=COMPLETED，status 可选：READY、RUNNING、COMPLETED、FAILED 或 INTERRUPTED；每页 1–50 条，最大偏移量 10000。按最后更新时间倒序排列并提供 has_more，只返回批次 ID、状态、已完成/总 SKU 数和时间戳，不返回商品素材、文件路径或原始请求。

服务重启后，遗留在文件中的 RUNNING 批次在历史列表中只读显示为 INTERRUPTED；点击查看旧批次时，现有详情接口负责落盘状态核对。切换历史批次会清除浏览器当前的批次审核范围，避免把另一个批次的审核筛选误当作当前批次；如需审核，需要重新点击「审核当前批次」。

历史列表会跳过损坏的批次状态文件和符号链接，仅展示有效本机记录。此功能不会自动删除原图或评测账本，不会生成新海报、调用 Qwen 或执行 OCR。已经完成的 M6 证据应继续保留，存储清理需要另行设计有确认和保留策略的安全入口。

