# 历史阶段记录（只作为工程决策及教学材料）

这里归档 M1–M3b 开发阶段留下的设计解释、样本协议和练习。历史文档的“下一步”“尚未实现”“CI 只模拟”等说法是**写作当时的状态**，并不保证反映当前 `main` 的能力。

**先读：** [当前 README](../../README.md) 和 [文档索引](../README.md)。

| 归档文件 | 记录内容 |
| --- | --- |
| [learning-checklist.md](learning-checklist.md) | 最初的 M1 动手技能练习 |
| [m2a-brief-to-spec.md](m2a-brief-to-spec.md) | Brief 结构化编译初版，彼时尚无持久化 |
| [m2b-checkpoint.md](m2b-checkpoint.md) | 初次接入 LangGraph SQLite Checkpointer 与人工审批 |
| [m2c-reliability.md](m2c-reliability.md) | 单机幂等、UNKNOWN 与事件记录初版 |
| [m3a-acceptance.md](m3a-acceptance.md) | 首版 OCR/规则 Acceptance Gate |
| [m3b-evaluation.md](m3b-evaluation.md) | 手工 OCR 文本集与混淆矩阵设计 |
| [m3b-image-replay.md](m3b-image-replay.md) | 程序合成 PNG 的初次回放方案 |

历史文档保留是为了复盘架构取舍，并非继续按原步骤开发的建议。真实 OCR Linux 验证、CI 手动触发规则和价格空间检查，见当前文档。
