# Phase 1: Python 版本与依赖升级 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-16
**Phase:** 01-python-langchain-upgrade
**Areas discussed:** 虚拟环境策略，依赖升级策略，兼容性测试范围，回滚方案

---

## 虚拟环境策略

| Option | Description | Selected |
|--------|-------------|----------|
| `.venv` + venv | Python 标准虚拟环境，与项目结构一致 | ✓ |
| `env/` + venv | 常见替代命名 | |
| conda | 重量级，适合数据科学项目 | |

**User's choice:** `.venv` + venv（推荐方案）
**Notes:** 用户确认采用推荐方案

---

## 依赖升级策略

| Option | Description | Selected |
|--------|-------------|----------|
| 渐进式 | 先 Python，再 LangChain，逐步验证 | ✓ |
| 一次性升级 | 同时升级所有依赖 | |

**User's choice:** 渐进式（推荐方案）
**Notes:** 降低调试复杂度，问题容易定位

---

## 兼容性测试范围

| Option | Description | Selected |
|--------|-------------|----------|
| 单元测试 + 冒烟测试 | pytest + 核心功能手动验证 | ✓ |
| 仅单元测试 | 自动化测试覆盖 | |
| 仅手动测试 | 完全人工验证 | |

**User's choice:** 单元测试 + 冒烟测试（推荐方案）
**Notes:** 确保自动化和人工验证都覆盖

---

## 回滚方案

| Option | Description | Selected |
|--------|-------------|----------|
| git + backup 文件 | git 保存状态 + requirements.txt.backup | ✓ |
| 仅 git | 依赖 git 历史恢复 | |
| 仅 backup 文件 | 手动备份恢复 | |

**User's choice:** git + backup 文件（推荐方案）
**Notes:** 双重保障，回滚更可靠

---

## Claude's Discretion

无特殊自由裁量领域 — 用户确认所有推荐方案。

## Deferred Ideas

无 — 讨论严格限制在阶段范围内。
