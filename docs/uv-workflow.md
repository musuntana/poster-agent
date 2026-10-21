# uv-first 开发与依赖管理（2026-10）

本项目以 **uv + pyproject.toml + uv.lock** 为唯一依赖来源。要求 Python 3.12；`uv` 可以在 Mac M1 上安装独立的 ARM64 Python，无需改变系统 Python 3.14。项目是 FastAPI 应用，而非需要发布 wheel 的库，使用 `[tool.uv] package=false`。该配置遵循 uv 官方项目、依赖组与锁文件机制。

## 首次配置（Mac/Linux）

```bash
# 先安装 uv，参照 https://docs.astral.sh/uv/getting-started/installation/
uv --version
git pull --ff-only origin main
uv python install 3.12
uv sync --locked
cp .env.example .env
uv run --locked python -m scripts.generate_demo
uv run --locked python -m pytest -q
uv run --locked ruff check app scripts evals tests
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

不必自己创建 venv 或 `source .venv/bin/activate`；uv 在仓库根自动管理 .venv。开发环境在 `[dependency-groups].dev` 中含 pytest + Ruff；Ruff 现阶段只启动有限的可靠性规则（E4/E9/F63/F7/F82），不把大规模代码格式重写混入本次迁移。

`uv sync --locked` 和 `uv run --locked` 会确保提交的 `uv.lock` 与项目依赖声明一致；`uv.lock` 必须由 **uv lock** 自动生成并纳入 Git，不可手动编辑。如果锁文件落后，命令会报错而不是悄悄更新。要修改依赖请使用：

```bash
uv add '<package-name>=x.y'
uv add --dev '<tool-name>=x.y'
uv add --optional ocr 'paddleocr==3.3.0'
uv lock --check
uv sync --locked
```

上述 `uv add` 默认更新 `pyproject.toml` 和锁文件；一次提交这两个文件。不要再编辑已经废弃的 `requirements*.txt`。

## OCR 重依赖按需安装

**日常开发不下载 OCR 模型**。PaddleOCR 3.3.0 在锁文件的 `[project.optional-dependencies].ocr` 中；PaddlePaddle 3.2.0 CPU 推理引擎使用官方平台专属源。安装顺序：

```bash
uv sync --locked --extra ocr
uv pip install --python .venv/bin/python paddlepaddle==3.2.0 \
  --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/
uv run --no-sync python -m scripts.ocr_probe
uv run --no-sync python -m scripts.generate_demo
uv run --no-sync python -m scripts.ocr_probe --infer \
  --image data/examples/sample_preview.png
```

对于 Mac M1，需先确认 PaddlePaddle 官方源提供与 ARM64/Python3.12 相符的 wheel；若安装失败应按官方文档排查，不要声称真实 OCR 已成功。仅 Linux CPU 的真实 OCR 曾在 GitHub Actions 上成功验证。

因为 PaddlePaddle 引擎由平台专属索引单独安装，**它不在 uv.lock 的默认依赖环境中**。真实 OCR 命令显式使用 `uv run --no-sync`，防止 uv 自动精确同步时移除该特殊安装的引擎。需要重建环境时先 `uv sync --locked --extra ocr`，再安装引擎。不要通过 `uv run --locked` 隐式删除 OCR 引擎后再测。

此处的 PaddleOCR Python 包版本由 uv.lock 固定；额外的 PaddlePaddle 平台包则由固定版本号和官方索引约束。它是**有说明的混合安装机制**，不能声称 PaddlePaddle 引擎也完全锁在 uv.lock 中。

## GitHub Actions 与配额

- `CI`：仅在 Draft PR → Ready for review、添加 `ci:run` 标签，或 `workflow_dispatch` 时启动。不会每次 push 就安装依赖。
- `quick`：`uv lock --check`、`uv sync --locked`、Ruff 安全检查、compileall、pytest。
- `full`：手动执行快速验证外的离线回放和样本生成。
- `Real PaddleOCR CPU`：**只手动启动**。安装 `--extra ocr` 和官方 PaddlePaddle CPU 包，再以 `uv run --no-sync` 运行真实模型；不能把此 CI 成功当作 Mac M1 的实测。
- `astral-sh/setup-uv` 固定版本并启用缓存，缓存依据 `uv.lock` 变动失效。保留原先的轻量运行频率；不会自动启动昂贵 OCR。

## uv 官方参考

- https://docs.astral.sh/uv/guides/projects/
- https://docs.astral.sh/uv/concepts/dependencies/
- https://docs.astral.sh/uv/concepts/projects/sync/
- https://docs.astral.sh/uv/guides/integration/github/
