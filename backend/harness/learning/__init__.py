# -*- coding: utf-8 -*-
"""
持续学习模块 —— 评分聚合 + 经验回灌

QAReviewer 评分 → Evaluator 聚合分析 → FeedbackLoop 回灌经验池
→ 下次相似需求自动注入 few-shot 示例 + 警告

闭环：
  任务完成 → QA 评分 → 成功(≥7) → 存入 exp_pool (positive)
                      → 失败(<5) → 分析错误模式 → 存入 exp_pool (negative)
  → 下次相似需求 → recall() → few-shot 示例 + 警告 → 注入 Prompt
"""

from dataclasses import dataclass, field

from harness.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class EvaluationReport:
    """评估报告"""
    requirement: str
    overall_rating: float
    dimensions: dict                    # {correctness: 8, code_quality: 7, ...}
    passed: bool
    error_patterns: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    is_success: bool = True             # 是否作为正面经验存储


class Evaluator:
    """QA 评分聚合器 —— 分析审查报告，提取可复用的经验"""

    SUCCESS_THRESHOLD = 7.0    # >= 此分 → 正面经验
    FAILURE_THRESHOLD = 5.0    # < 此分 → 负面经验

    @staticmethod
    def evaluate(qa_result: dict, requirement: str) -> EvaluationReport:
        """
        分析 QA 审查报告，生成评估结果。

        Args:
            qa_result: QAReviewer 的结构化输出
            requirement: 用户需求文本

        Returns:
            EvaluationReport
        """
        rating = qa_result.get("overall_rating", 7)
        dimensions = qa_result.get("dimensions", {})
        passed = qa_result.get("passed", True)
        critical_issues = qa_result.get("critical_issues", [])
        suggestions = qa_result.get("suggestions", [])

        # 识别错误模式（从 critical_issues 中提取可通用的模式）
        error_patterns = Evaluator._extract_patterns(critical_issues)

        is_success = rating >= Evaluator.SUCCESS_THRESHOLD

        report = EvaluationReport(
            requirement=requirement,
            overall_rating=rating,
            dimensions=dimensions,
            passed=passed,
            error_patterns=error_patterns,
            suggestions=suggestions,
            is_success=is_success,
        )

        logger.info(
            f"[Evaluator] 评分={rating}, passed={passed}, "
            f"is_success={is_success}, patterns={len(error_patterns)}"
        )
        return report

    @staticmethod
    def _extract_patterns(issues: list[str]) -> list[str]:
        """
        从具体问题中提取通用错误模式。

        例如：
        - "script.js 第 45 行使用了 innerHTML" → "避免使用 innerHTML"
        - "删除操作没有确认弹窗" → "危险操作需加确认弹窗"
        """
        patterns = []
        for issue in issues:
            issue_lower = issue.lower()

            if "innerhtml" in issue_lower:
                patterns.append("禁止使用 innerHTML，用 textContent 或 createElement 替代")
            elif "eval" in issue_lower:
                patterns.append("禁止使用 eval()")
            elif "document.write" in issue_lower:
                patterns.append("禁止使用 document.write()")
            elif "localstorage" in issue_lower and ("json" in issue_lower or "parse" in issue_lower):
                patterns.append("localStorage 读写必须做 JSON.parse/stringify 异常处理")
            elif "删除" in issue and ("确认" in issue or "弹窗" in issue):
                patterns.append("危险操作（删除/清空）需加确认弹窗")
            elif "空" in issue and ("状态" in issue or "数据" in issue):
                patterns.append("需处理空状态/无数据时的 UI 展示")
            elif "响应式" in issue or "responsive" in issue_lower:
                patterns.append("需考虑移动端响应式布局")
            elif "xss" in issue_lower or "注入" in issue:
                patterns.append("用户输入需做 XSS 防护（避免直接插入 HTML）")
            else:
                # 通用化处理：去掉具体文件名、行号
                import re
                generalized = re.sub(r'第\s*\d+\s*行', '', issue)
                generalized = re.sub(r'\b\w+\.\w+\b', '', generalized)  # 去掉文件名
                generalized = generalized.strip()
                if len(generalized) > 10:
                    patterns.append(generalized)

        # 去重
        seen = set()
        unique = []
        for p in patterns:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique


class FeedbackLoop:
    """经验回灌闭环 —— 连接 ExperiencePool 和 Agent 工作流

    两个关键注入点：
    1. 任务开始前：recall 相似经验 → 注入 Prompt（few-shot + 警告）
    2. 任务完成后：QA 评分 → store 正/负面经验
    """

    def __init__(self, experience_pool):
        self.pool = experience_pool
        self.evaluator = Evaluator()

    def inject_experience(self, requirement: str,
                          system_prompt: str) -> str:
        """
        在任务开始前，将历史经验注入 System Prompt。

        Args:
            requirement: 用户需求
            system_prompt: 原始系统提示词

        Returns:
            增强后的系统提示词（追加 few-shot 示例 + 警告）
        """
        # 检索相似正面经验
        few_shot = self.pool.get_few_shot_text(requirement, n_examples=2)

        # 检索相关失败案例的警告
        warnings = self.pool.get_warnings_text(requirement)

        # 组装增强 prompt
        if not few_shot and not warnings:
            return system_prompt

        enhancements = []
        if few_shot:
            enhancements.append(few_shot)
        if warnings:
            enhancements.append(warnings)

        return system_prompt + "\n\n" + "\n\n".join(enhancements)

    def learn_from_result(self, requirement: str, complexity: str,
                          code_files: list, qa_result: dict = None):
        """
        任务完成后，从结果中学习。

        Args:
            requirement: 用户需求
            complexity: XS/S/M/L
            code_files: 生成的代码文件列表
            qa_result: QA 审查报告（可选，无 QA 时用默认评分）
        """
        if qa_result:
            report = self.evaluator.evaluate(qa_result, requirement)
        else:
            # 无 QA 审查（XS/S 复杂度），默认评分 7
            report = EvaluationReport(
                requirement=requirement,
                overall_rating=7.0,
                dimensions={},
                passed=True,
                is_success=True,
            )

        # 构建代码摘要
        code_summary = FeedbackLoop._build_code_summary(code_files)

        if report.is_success:
            # 正面经验
            self.pool.store(
                requirement=requirement,
                complexity=complexity,
                code_summary=code_summary,
                rating=report.overall_rating,
                file_count=len(code_files),
                total_lines=sum(
                    f.get("content", "").count('\n') + 1
                    for f in code_files
                ),
                metadata={
                    "dimensions": report.dimensions,
                    "suggestions": report.suggestions,
                },
            )
        else:
            # 负面经验：存储失败案例
            for pattern in report.error_patterns:
                self.pool.store_failure(
                    requirement=requirement,
                    error_pattern=pattern,
                )
            # 也存储一条低评分记录（供 recall 时过滤）
            self.pool.store(
                requirement=requirement,
                complexity=complexity,
                code_summary=code_summary,
                rating=report.overall_rating,
                file_count=len(code_files),
                metadata={"type": "low_quality"},
            )

        logger.info(
            f"[FeedbackLoop] 学习完成: rating={report.overall_rating}, "
            f"is_success={report.is_success}"
        )

        return report

    @staticmethod
    def _build_code_summary(code_files: list) -> str:
        """构建代码方案摘要文本"""
        if not code_files:
            return "无代码文件"

        parts = []
        for f in code_files:
            fname = f.get("filename", "unknown")
            content = f.get("content", "")
            lines = content.count('\n') + 1 if content else 0
            # 提取前 100 字符作为预览
            preview = content[:100].replace('\n', ' ').strip()
            parts.append(f"{fname}({lines}行): {preview}...")

        return " | ".join(parts[:5])  # 最多 5 个文件

    def stats(self) -> dict:
        """学习统计"""
        return self.pool.stats()
