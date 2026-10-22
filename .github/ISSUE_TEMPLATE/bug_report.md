---
name: Bug report / 缺陷反馈
about: 报告可复现问题（不提交密钥、真实客户图片或漏洞利用细节）
title: "[Bug] "
labels: bug
---

### 当前问题

描述观察到的实际行为和预期行为。

### 最小复现

仅使用**合成/已授权样本**，不要贴真实客户图、密钥、个人隐私、未脱敏日志。

### 环境

- 操作系统与 CPU 架构：
- Python 版本（建议 3.12）：
- uv 版本：
- Poster Agent commit SHA：
- `background_mode`：local / ai（切勿提供 API Key）
- OCR：未运行 / Mock / PaddleOCR 真正推理：

### 证据

请提供脱敏的堆栈、错误响应、相关 pytest 名称和测试结果。若图片包含真实商业素材请先私密沟通。

### 边界

**安全漏洞、凭据泄露或可直接利用的风险不要提交公开 Issue**，请先阅读 SECURITY.md。
