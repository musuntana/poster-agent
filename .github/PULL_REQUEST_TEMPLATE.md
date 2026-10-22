## 变更目的与范围

请描述本 PR 解决的用户/工程问题，复用了哪些现有能力，哪些内容刻意不在本次范围。

## 实现与验证

- [ ] 已检查并复用现有 FastAPI/Pillow/uv/评测模块，避免重复造轮子
- [ ] 新增或更新有意义的自动化测试
- [ ] `uv run --locked ruff check app tests scripts evals` 已通过
- [ ] `uv run --locked python -m pytest -q` 已通过，或明确说明尚未验证
- [ ] `uv run --locked python -m scripts.check_repository_readiness` 无敏感文件警告
- [ ] 如果涉及依赖，`pyproject.toml` 与 uv 生成的 `uv.lock` 一起更新

## 证据与可信边界

说明本次实际检查了什么，并区分：静态审查、模拟 OCR、真实 OCR、真实商品人工验收与生产环境测试。不要把测试通过率当成商品质量准确率。

## 数据安全与费用

- [ ] 未提交真实客户图像、API Key、`.env`、数据库或本地评测产物
- [ ] 未默认触发收费的模型调用、完整 OCR 模型下载
- [ ] 前端/后端权限边界与兼容性风险已说明

## CI 约定

提交 Draft PR → Ready 后才跑一次 quick。每次 push 不自动触发；真实 PaddleOCR 和 full benchmark 只能明确手动启动。

## 已知限制与下一步

请写清没有完成/没有实测的内容，以及是否影响其他用户或需要迁移。
