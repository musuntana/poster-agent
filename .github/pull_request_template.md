## 变更目的与范围 / Summary

- 用户或工程问题：
- 本次变更与明确非目标：
- 已复用的现有模块、成熟开源项目或官方集成：

## 实现与验证 / Validation

- [ ] 新增或更新了有意义的自动化测试。
- [ ] `uv run --locked ruff check app tests scripts evals` 已通过；如未运行，已说明原因。
- [ ] `uv run --locked python -m pytest -q` 已通过；如未运行，已说明原因。
- [ ] `uv run --locked python -m scripts.check_repository_readiness` 已运行并检查输出。
- [ ] 已记录精确命令、结果，以及验证对应的 commit SHA。
- [ ] 如有依赖变化，已通过 `uv add` 更新并提交 `pyproject.toml` 与 `uv.lock`。

## 证据与可信边界 / Evidence

- [ ] 明确区分合成/Mock、真实模型 OCR、真实商品人工评审与生产环境验证。
- [ ] 未将 CI 通过或合成样本结果表述为真实商品质量、客户准确率或生产就绪证明。
- 证据链接、命令与结果：

## 数据安全、隐私与费用

- [ ] 未提交 API Key、`.env`、真实客户图片、私有批次账本、数据库或本地评测产物。
- [ ] 未默认触发收费模型调用或昂贵的 OCR 模型下载。
- 数据隐私、运行成本、兼容性与迁移风险：

## CI 约定

- [ ] 按仓库策略在 Draft PR 准备好后切换为 Ready；不因每次 push 触发 CI。
- [ ] 若最新 HEAD 尚未验证，已明确标注；旧 commit 的 CI 结果不视为新 HEAD 的验证。

## 已知限制与后续工作

- 未完成或未实测的内容：
- 需要的人工验收、授权或本地证据：
