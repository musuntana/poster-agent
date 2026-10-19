# Poster Agent · M1（可运行的海报生成器）

M1 只实现一个真实闭环：**上传 SKU → 选择本地/真实 AI 背景 → 精确合成文案/Logo → 预览 → 修改价格 → 保存版本 → 导出**。

> 当前是单机学习版：没有用户登录、鉴权、请求队列和并发写入事务机制；请只在 `127.0.0.1` 访问，**不要直接部署公网**。本地渐变背景不是 AI 生图。真实 Qwen API 需要你自己的 Key，并会产生计费。

## 0. 环境

- Python 3.11—3.13（建议 3.11/3.12），macOS M1/M2/M3 适用。
- 支持中文的系统字体（Mac 建议 PingFang；Linux 建议 Noto Sans CJK）。
- 推荐上传透明 PNG 商品图（否则系统会保留白底并放进商品卡片，不会虚构自动抠图）。

## 1. 安装

```bash
git clone git@github.com:musuntana/poster-agent.git
cd poster-agent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

如出现“找不到中文字体”，编辑 `.env` 中的 `POSTER_FONT_PATH`：

```bash
# Apple macOS 典型位置，先检查：ls /System/Library/Fonts/PingFang.ttc
POSTER_FONT_PATH=/System/Library/Fonts/PingFang.ttc
```

## 2. 先生成纯本地样例（不需要 API Key）

```bash
python -m scripts.generate_demo
```

会生成：

- `data/examples/sample_product.png`
- `data/examples/sample_logo.png`
- `data/examples/sample_preview.png`

打开样例 `open data/examples/sample_preview.png`（macOS）。

## 3. 启动 Web 服务

```bash
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 **http://127.0.0.1:8000**。使用 sample_product.png 和 sample_logo.png 试用“本地渐变模式”。

API 文档：http://127.0.0.1:8000/docs

## 4. 启用真实 Qwen-Image 背景（可选）

在阿里云百炼控制台获取 API Key 和对应区域的 **Workspace ID**，然后在本地 `.env` 中配置：

```ini
DASHSCOPE_API_KEY=你的Key
QWEN_IMAGE_ENDPOINT=https://你的WorkspaceId.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/images/generations
QWEN_IMAGE_MODEL=qwen-image-3.0
```

如果工作空间在新加坡，将域名换成 `你的WorkspaceId.ap-southeast-1.maas.aliyuncs.com`。**Key、模型服务地域必须匹配**。不要把 `.env` 提交到 GitHub。

然后重新启动服务，在页面中切换 **真实 Qwen-Image 模式**。

接口来源：<https://help.aliyun.com/en/model-studio/qwen-image-generation-and-editing-api-reference>（OpenAI 兼容 `/images/generations`，返回 `data[0].url`，图片 URL 约 24 小时有效；本项目会立即保存背景至本地）。

本项目真实 API 调用尚未使用你的 Key 进行实际联网测试；请以你本地调用结果为准。

## 5. 修改海报并下载

生成一张海报后，在右侧“修改内容”区域将价格从 199 修改成 189 并保存：

- 创建新版本 v2；
- 背景不会重新调用 Qwen API；
- 原图/Logo/背景保留不变；
- `data/posters/<id>/versions/` 保存不同版本的 PNG 与 JSON。

可手动测试接口：

```bash
curl http://127.0.0.1:8000/health
```

## 6. 运行测试

```bash
python -m pytest -q
```

应检查：数据校验、Pillow 合成、创建任务、改价格产生 v2、历史版本存在、非法文件被拒绝、AI Key 缺失时返回错误而不是偷偷用本地背景。

## 7. 项目结构

```text
app/
  main.py            FastAPI API / 网页入口
  schemas.py         Pydantic 规格及修订模型
  image_provider.py  真实 Qwen-Image API 背景生成
  compositor.py      Pillow 商品/Logo/文案图层合成
  storage.py         单机本地版本管理
  templates/index.html  最简 Web 页面
scripts/generate_demo.py  本地样例素材和预览图
tests/               自动化测试
data/examples/       本地生成的教学样例（运行脚本生成）
```

## M1 的明确限制

- 只有 1024×1024 正方形布局；没有多尺寸派生。
- 上传文件最大 10MB，仅允许 PNG/JPEG/WEBP；没有用户认证及安全审计。
- 中文字体必须在运行环境可用；Windows 需自己配置路径。
- 浏览器的图片生成请求目前同步等待；后续 M2/M5 改为异步任务和状态轮询。
- 版本保留在本机目录，不适合多台 Worker 并发写入。
- 标题过长时会报错要求缩短，而不是被裁剪。
- 暂不包含抠图/可视化拖拽/复杂品牌规则；不应称为企业级生产系统。

## 后续路线

M2：自然语言 → Spec Compiler → LangGraph Planner/State/Repair；M3：OCR + Brand Rules + VLM/Benchmark；M4：多规格派生；M5：PostgreSQL + Redis/Celery + Docker/账号与隔离；M6：消融实验、执行链路与成本监控。

参见 `docs/learning-checklist.md`。
