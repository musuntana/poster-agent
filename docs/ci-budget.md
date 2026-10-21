# CI 额度优化（2026-10）

## 改动目标

GitHub Actions 不再因**每次 push / 每次 PR synchronize** 就自动执行测试或下载 OCR 模型。工作流拆分为轻量校验、完整离线评测和昂贵的真实 OCR：

| 类型 | 触发时机 | 执行内容 | 自动触发？ |
|---|---|---|---|
| 快速 CI | 有代码变更的 PR 从 Draft 切换成 Ready for review | Ruff + Ruff + Python compileall + pytest | **仅一次** |
| 快速 CI（补测） | 给已有 PR 添加 `ci:run` 标签 | Python compileall + pytest | 明确标记时才触发 |
| 快速 CI（手动） | Actions → CI → Run workflow，选择 `quick` | Python compileall + pytest | 否 |
| 完整 CI（手动） | Actions → CI → Run workflow，选择 `full` | 快速 CI + 合成 OCR 评测 + 20 张 PNG + 产物上传 + demo | 否 |
| 真实 PaddleOCR | Actions → Real PaddleOCR CPU → Run workflow | 下载 CPU 模型、真实识别、证据验证；可选 20 图 | **只允许手动** |

不配置 `push`，不配置 `schedule`。合并 PR 到 `main` 不会再触发一遍普通 CI。昂贵的 OCR 工作流没有 `pull_request` 或 `push` 事件。

文档、README 或 Markdown 文件单独变化时，普通 CI 的 PR 路径过滤器默认不会启动 Runner。更改代码、测试、pyproject.toml、uv.lock 或工作流文件时，在 PR 切换到 Ready for review 的那一刻运行一次快速检查。

### 推荐日常流程

1. 多次提交代码到 `feature/...`：**都不会自动运行 CI**。
2. 创建 **Draft PR**，继续修改、测试代码。
3. 开发和本机测试稳定后，点击 **Ready for review**：仅执行一次快速 CI。
4. 如审查后又修改代码，**不会自动重新跑**。需要验证新 HEAD 时，移除旧的 `ci:run` 标签再重新添加，或者选择 Actions 手动运行。
5. 仅对重要里程碑或可能影响图片生成/评测的数据契约手动选择 `full`。
6. 仅在 OCR 引擎、模型或推理代码调整后，有明确需要时手动运行 `Real PaddleOCR CPU`。

**注意：** PR 如果创建时就处于 Ready 状态，不会触发 `ready_for_review` 事件。此时请添加 `ci:run` 标签或手动运行。标签首次使用可在 GitHub Issues/Labels 创建一个普通标签 `ci:run`；其他标签不会启动测试 Runner。

项目依赖已统一到 `pyproject.toml` + `uv.lock`；见 [uv 工作流](uv-workflow.md)。

### Actions 页面手动启动

- [CI 工作流](https://github.com/musuntana/poster-agent/actions/workflows/ci.yml) → **Run workflow** → 选分支 → 选 `quick` 或 `full`。
- [真实 OCR 工作流](https://github.com/musuntana/poster-agent/actions/workflows/real-ocr.yml) → **Run workflow** → 选择 `main` → `full_benchmark` 默认 false。

如使用已认证的 GitHub CLI：

```bash
# 指定需要检查的代码分支；新改动后要手动再跑
gh workflow run ci.yml --ref feature/my-change -f level=quick

# 重要功能验收时执行完整离线评测
gh workflow run ci.yml --ref feature/my-change -f level=full

# 只有真实 OCR 变更才执行，通常使用已合并的 main
gh workflow run real-ocr.yml --ref main -f full_benchmark=false
```

**不要因没有 CI 自动运行就认为代码已经通过测试。** 合并前，至少用本机 `uv run --locked python -m pytest -q`，或在 PR 的最终 commit 上显式触发一次快速 CI。CI 的状态对应特定 commit；如果最后一次 CI 后又改了代码，原来的通过结果不保证适用于新提交。

### 费用优化与重要限制

- 移除每次 push 触发后，高频 commit 本身不会消耗 CI Runner 分钟。
- `full` 才会运行额外回放、OCR 环境诊断和上传构建产物；默认 `quick` 只跑语法与 pytest。
- 完整离线产物保留时间从 14 天缩短为 **3 天**，减少存储占用。
- 对同一 PR/分支重复点击时，`concurrency.cancel-in-progress` 会取消尚未完成的前一轮；已经运行过的时间仍可能计入额度。
- **GitHub Branch protection / Rulesets**：如果仓库把 `CI / python-tests` 设为 *Required status check*，基于路径过滤、仅准备审查时触发及手动派发可能让检查长时间 Pending，阻止合并。此时应在仓库 Settings → Rules → Rulesets/Branch protection 审查要求，选择始终对需要合并的 PR 触发适用的检查，或者调整 Required checks 规则。**本次没有修改仓库分支保护设置。**
- GitHub 官方特别指出 workflow path filtering 可能导致必选检查处于 Pending；别把这个当成代码出错。
- 若 PR 在 Ready for review 后又更新，请显式补测；不会为节省额度而把旧 HEAD 的结果假装为新 HEAD 验证。
- 真实 OCR 的 Docker/PaddlePaddle 下载仍耗时且消耗 Runner 配额，因此只应按需运行。所有结果仅能证明合成样本上的模型链路，不能替代真实商品海报准确率评估。

参考：
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
