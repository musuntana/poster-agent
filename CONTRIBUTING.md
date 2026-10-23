# Contributing to Poster Agent / 项目贡献指南

> **状态说明（2026-10-23）：** 仓库当前为 **Public**，但尚未选择或添加开源许可证。公开可见不等于授予代码与素材的复制、修改或再分发许可；本页说明贡献流程，不代表项目已完成正式开源授权。

## 贡献原则

优先复用成熟开源库和官方集成，不重复构建通用能力；每次 PR 聚焦一个可复核的工程问题。已有 FastAPI/Pillow/LangGraph/SQLite/uv/PaddleOCR 模块应先检查复用可能。

本项目当前是**单机本地 Demo**，无鉴权、多租户隔离或生产级任务队列。不能将模拟 OCR、合成图片测试、smoke test 冒充客户场景准确率，也不能在未获许可情况下提交真实客户商品数据。

## 开发准备（Python 3.12 + uv）

```bash
uv python install 3.12
uv sync --locked
uv run --locked ruff check app tests scripts evals
uv run --locked python -m pytest -q
uv run --locked python -m scripts.check_repository_readiness
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

无需手动创建 venv；运行前确保本机有中文字体。本地运行默认使用免费 `background_mode=local`。真实 Qwen API 可能收费，不能作为 CI 测试默认路径。

PaddleOCR 是可选的模型依赖，不会在默认 `uv sync --locked` 下载推理模型。要运行真实 OCR，依照 [uv 手册](docs/uv-workflow.md) 单独准备平台专属 PaddlePaddle 环境；仅 Linux CPU 单图曾经实测成功，不能据此声称 Mac M1 已实测成功。

## PR 工作流与 CI 费用

1. 从 `main` 创建主题分支；先确认是否已有对应接口/工作流，再修改代码。
2. 增加可重复执行的 pytest；Mock OCR 与真实 PaddleOCR 的证据必须分开说明。
3. 修改依赖请通过 `uv add`，同时提交 `pyproject.toml` 和生成的 `uv.lock`，禁止手工修改锁文件。
4. PR 默认以 **Draft** 创建。准备好后转换为 **Ready for review**，触发一次快速 CI；每次 push **不会**自动触发 CI。
5. 修改后需再次验证，请在 GitHub Actions 手动运行 quick 或重新使用 `ci:run` 标签。完整图片回放及真实 PaddleOCR 是**手动工作流**，不要为了日常文档改动反复触发。
6. 仅合并通过 CI、明确验证边界的变更；不可提交未知运行结果并声称成功。

PR 描述请注明：用户可见变化、受影响 API/文件、测试证据、是否使用真实模型、潜在数据隐私/成本风险、已知未完成事项。

## 数据与安全

- 禁止提交 `.env`、API 密钥、客户素材、产品图片原图、`data/posters/`、`data/eval_runs/`、私有批次导入账本或 SQLite 运行状态。
- 已有 `data/examples/brand_rules.sample.json` 属于公开安全示例；实际数据只在受信本地保管。
- 使用 `uv run --locked python -m scripts.check_repository_readiness` 审查 Git 索引中的意外运行时文件和高可信度密钥模式；它**不是完整秘密扫描**。
- 发现安全漏洞请先阅读 [SECURITY.md](SECURITY.md)，不要提交包含攻击细节、真实凭据或客户隐私的公开 Issue。
- 若要正式以开源项目方式发布或接受外部复用，必须先由所有者完成 [发布准备清单](docs/open-source-release.md) 中的许可证、权属、历史提交与隐私评审。

我们尤其欢迎：4:5 真实图片的匿名化可复现问题、OCR 空结果与空间框置信度处理、Mac ARM64 的可验证安装报告、测试覆盖与文档修正。
