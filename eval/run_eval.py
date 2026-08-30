# -*- coding: utf-8 -*-
"""
Eval Runner —— 生成质量基线评估

驱动真实的 ToolCallLoop 生成代码（评估的是真实生成质量，不是 mock），
然后对每个任务跑断言检查器，输出可对比的基线报告。

用法：
    cd backend && PYTHONPATH=. python ../eval/run_eval.py
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --tasks t01 t02   # 只跑指定任务
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --no-preview       # 跳过浏览器验证（CI 快跑）

输出：
    eval/results/baseline_<timestamp>.json   # 完整结果
    eval/results/baseline_<timestamp>.md     # 可读摘要
    eval/results/latest.json -> 上述 json    # 最新软链
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# 默认从 backend/ 运行（PYTHONPATH=.），否则用本文件定位
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import yaml

from harness.state.workspace import WorkspaceFS
from harness.state.agent_state import AgentState
from harness.tools.registry import create_tool_registry
from harness.constraints.hooks import create_default_hook_manager
from harness.runtime import ToolCallLoop


# ---------- 数据结构 ----------

@dataclass
class AssertionResult:
    type: str
    passed: bool
    detail: str = ""


@dataclass
class TaskResult:
    id: str
    name: str
    level: int
    passed: bool
    assertions: list = field(default_factory=list)  # [AssertionResult]
    duration_s: float = 0.0
    error: str = ""
    files: list = field(default_factory=list)


# ---------- 断言检查器 ----------

class AssertionChecker:
    """对生成结果跑断言。复用 preview_runner 做 preview_no_error 检查。"""

    def __init__(self, workspace: WorkspaceFS, run_preview: bool = True):
        self.ws = workspace
        self.run_preview = run_preview
        self._preview_cache: dict = {}

    def _read(self, filename: str) -> Optional[str]:
        try:
            return self.ws.read(filename)
        except Exception:
            return None

    def _resolve_content(self, filename: str):
        """返回用于内容检查的文件内容，兼容脚手架把资源放在子目录的约定。

        - 精确文件存在 → 返回其内容（exact=True）
        - 否则回退到工作区内所有「同扩展名」文件拼接内容（exact=False）：
          ``style.css`` → 所有 ``*.css``，``script.js`` → 所有 ``*.js``。
          这样断言「样式表含 gradient / JS 含 localStorage」不再因路径
          （``css/style.css``、``js/main.js``）或文件名差异而假阴性。
        """
        if self.ws.exists(filename):
            try:
                return self.ws.read(filename), True
            except Exception:
                pass
        ext = os.path.splitext(filename)[1].lower()
        parts = []
        for f in self.ws.list():
            if ext and f.lower().endswith(ext):
                try:
                    parts.append(self.ws.read(f))
                except Exception:
                    continue
        return "\n".join(parts), False

    def check(self, assertion: dict) -> AssertionResult:
        t = assertion["type"]
        try:
            return getattr(self, f"_check_{t}")(assertion)
        except AttributeError:
            return AssertionResult(t, False, f"未知断言类型: {t}")
        except Exception as e:
            return AssertionResult(t, False, f"检查异常: {e}")

    def _check_file_exists(self, a) -> AssertionResult:
        ok = self.ws.exists(a["filename"])
        return AssertionResult("file_exists", ok,
                               "" if ok else f"{a['filename']} 不存在")

    def _check_content_contains(self, a) -> AssertionResult:
        content, exact = self._resolve_content(a["filename"])
        if not content:
            return AssertionResult("content_contains", False,
                                   f"{a['filename']} 不存在（含同扩展名回退）")
        ok = a["text"] in content
        where = "" if exact else "（回退匹配同扩展名文件）"
        return AssertionResult("content_contains", ok,
                               "" if ok else f"未找到: {a['text']!r}{where}")

    def _check_content_not_contains(self, a) -> AssertionResult:
        content, exact = self._resolve_content(a["filename"])
        if not content:
            return AssertionResult("content_not_contains", True,
                                   f"{a['filename']} 不存在，视为不包含")
        # 词边界匹配，避免把 reveal / medieval 等误判为含 eval
        ok = not re.search(r"(?:\b|(?<![\w.]))" + re.escape(a["text"]) + r"(?:\b|(?![\w.]))", content)
        where = "" if exact else "（同扩展名回退）"
        return AssertionResult("content_not_contains", ok,
                               "" if ok else f"发现禁用内容: {a['text']!r}{where}")

    def _check_file_min_lines(self, a) -> AssertionResult:
        content, exact = self._resolve_content(a["filename"])
        if not content:
            return AssertionResult("file_min_lines", False,
                                   f"{a['filename']} 不存在")
        lines = content.count("\n") + 1
        ok = lines >= a["min"]
        return AssertionResult("file_min_lines", ok,
                               f"{lines} 行 (要求 ≥{a['min']})")

    def _check_html_has_element(self, a) -> AssertionResult:
        content = self._read("index.html")
        if content is None:
            return AssertionResult("html_has_element", False, "index.html 不存在")
        # 轻量选择器解析：只支持标签名 / .class / #id（覆盖 eval 用例）
        ok = self._selector_match(content, a["selector"])
        return AssertionResult("html_has_element", ok,
                               "" if ok else f"未匹配选择器: {a['selector']}")

    def _check_preview_no_error(self, a) -> AssertionResult:
        if not self.run_preview:
            return AssertionResult("preview_no_error", True, "跳过(--no-preview)")
        if not self.ws.exists("index.html"):
            return AssertionResult("preview_no_error", False, "index.html 不存在")
        report = self._run_preview_cached()
        errs = report.get("errors", [])
        if not report.get("available", True):
            return AssertionResult("preview_no_error", True, "浏览器不可用，跳过")
        ok = len(errs) == 0
        detail = "无错误" if ok else "; ".join(
            f"[{e.get('type')}] {e.get('message', '')[:60]}" for e in errs[:3]
        )
        return AssertionResult("preview_no_error", ok, detail)

    # ---------- 辅助 ----------

    def _run_preview_cached(self) -> dict:
        if self._preview_cache:
            return self._preview_cache
        try:
            from harness.tools.preview_runner import run_preview_in_browser
            self._preview_cache = run_preview_in_browser(self.ws.path / "index.html")
        except Exception as e:
            self._preview_cache = {"available": False, "errors": [], "skip_reason": str(e)}
        return self._preview_cache

    @staticmethod
    def _selector_match(html: str, selector: str) -> bool:
        import re
        sel = selector.strip()
        if sel.startswith(".") :
            return bool(re.search(rf'class\s*=\s*"[^"]*\b{re.escape(sel[1:])}\b', html))
        if sel.startswith("#"):
            return bool(re.search(rf'id\s*=\s*"{re.escape(sel[1:])}"', html))
        # 标签名
        return bool(re.search(rf"<{re.escape(sel)}[\s>]", html, re.IGNORECASE))


# ---------- 单任务执行 ----------

def run_one_task(task: dict, args) -> TaskResult:
    """驱动 ToolCallLoop 生成一个任务，然后检查断言"""
    tid = task["id"]
    result = TaskResult(id=tid, name=task["name"], level=task["level"], passed=False)
    t0 = time.time()

    # 每个任务独立临时工作区
    eval_base = Path("/tmp/talk2code_eval")
    ws = WorkspaceFS(user_id=0, requirement_id=int(tid.lstrip("t")), base_dir=eval_base)
    if ws.path.exists():
        shutil.rmtree(ws.path)
    ws.init([])  # 空工作区

    tools = create_tool_registry()
    hooks = create_default_hook_manager()
    loop = ToolCallLoop(workspace=ws, git=None, tools=tools, hooks=hooks)

    state: AgentState = {
        "requirement_id": int(tid.lstrip("t")),
        "user_id": 0,
        "requirement_content": task["requirement"],
        "plan": None,
        "current_step": "starting",
        "code_files": [],
        "validation_result": None,
        "retry_count": 0,
        "error": None,
        "dialogue_history": [],
        "metadata": {},
        "tool_call_count": 0,
        "no_progress_count": 0,
        "last_file_list": [],
        "hook_failures": {},
        "visual_style": None,
    }

    try:
        final_state = loop.run(state)
        if final_state.get("error"):
            result.error = str(final_state["error"])
    except Exception as e:
        import traceback
        result.error = f"生成异常: {e}\n{traceback.format_exc()}"

    result.files = ws.list()
    result.duration_s = round(time.time() - t0, 1)

    # 断言检查
    checker = AssertionChecker(ws, run_preview=not args.no_preview)
    for a in task.get("assertions", []):
        result.assertions.append(asdict(checker.check(a)))

    result.passed = all(ar["passed"] for ar in result.assertions) and not result.error
    # 清理临时工作区
    try:
        shutil.rmtree(ws.path)
    except Exception:
        pass
    return result


# ---------- 报告 ----------

def write_reports(results: list[TaskResult], tasks_run: int):
    ts = time.strftime("%Y%m%d_%H%M%S")
    results_dir = _HERE / "results"
    results_dir.mkdir(exist_ok=True)
    json_path = results_dir / f"baseline_{ts}.json"
    md_path = results_dir / f"baseline_{ts}.md"

    passed = sum(1 for r in results if r.passed)
    by_level = {}
    for r in results:
        by_level.setdefault(r.level, {"pass": 0, "total": 0})
        by_level[r.level]["total"] += 1
        if r.passed:
            by_level[r.level]["pass"] += 1

    data = {
        "timestamp": ts,
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results) * 100, 1) if results else 0,
        "by_level": by_level,
        "results": [asdict(r) for r in results],
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # Markdown 摘要
    lines = [
        f"# Eval 基线报告 ({ts})",
        "",
        f"- **通过率**: {passed}/{len(results)} ({data['pass_rate']}%)",
        f"- **任务数**: {len(results)}",
        "",
        "## 按难度",
        "",
        "| 难度 | 通过 | 总数 |",
        "|---|---|---|",
    ]
    for lv in sorted(by_level):
        lines.append(f"| L{lv} | {by_level[lv]['pass']} | {by_level[lv]['total']} |")
    lines += ["", "## 明细", "", "| ID | 名称 | 通过 | 耗时 | 失败项 |", "|---|---|---|---|---|"]
    for r in results:
        fails = [a["type"] for a in r.assertions if not a["passed"]]
        mark = "✅" if r.passed else "❌"
        lines.append(f"| {r.id} | {r.name} | {mark} | {r.duration_s}s | {', '.join(fails) or '-'} |")
    md_path.write_text("\n".join(lines))

    # latest 软链（覆盖）
    latest = results_dir / "latest.json"
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    latest.symlink_to(json_path.name)
    return json_path, md_path, data


# ---------- 对比 ----------

def compare(latest: dict, baseline_path: Path) -> str:
    """对比当前结果与历史基线，输出回归提示"""
    try:
        old = json.loads(baseline_path.read_text())
    except Exception:
        return ""
    old_map = {r["id"]: r["passed"] for r in old.get("results", [])}
    new_map = {r["id"]: r["passed"] for r in latest["results"]}
    regressions = [tid for tid in new_map if old_map.get(tid) and not new_map[tid]]
    improvements = [tid for tid in new_map if not old_map.get(tid) and new_map[tid]]
    parts = []
    if regressions:
        parts.append(f"⚠️ 回归 {len(regressions)} 个: {', '.join(regressions)}")
    if improvements:
        parts.append(f"⬆️ 改善 {len(improvements)} 个: {', '.join(improvements)}")
    if not parts:
        parts.append("无回归/改善")
    parts.append(f"通过率 {old.get('pass_rate', 0)}% → {latest['pass_rate']}%")
    return " | ".join(parts)


# ---------- 主 ----------

def main():
    parser = argparse.ArgumentParser(description="Talk2Code 生成质量 Eval")
    parser.add_argument("--tasks", nargs="*", help="只跑指定任务 id（如 t01 t02）")
    parser.add_argument("--no-preview", action="store_true", help="跳过浏览器验证（快）")
    parser.add_argument("--compare", metavar="BASELINE_JSON", help="对比历史基线")
    args = parser.parse_args()

    tasks = yaml.safe_load((_HERE / "tasks" / "tasks.yaml").read_text())["tasks"]
    if args.tasks:
        want = set(args.tasks)
        tasks = [t for t in tasks if t["id"] in want]
        if not tasks:
            print(f"未找到任务: {args.tasks}")
            sys.exit(1)

    print(f"Eval: {len(tasks)} 个任务 (preview={'off' if args.no_preview else 'on'})\n")

    results = []
    for i, task in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {task['id']} {task['name']} ... ", end="", flush=True)
        r = run_one_task(task, args)
        mark = "✅" if r.passed else "❌"
        print(f"{mark} ({r.duration_s}s)" + (f"  {r.error}" if r.error else ""))
        results.append(r)

    json_path, md_path, data = write_reports(results, len(tasks))
    print(f"\n通过率: {data['passed']}/{data['total']} ({data['pass_rate']}%)")
    print(f"报告: {md_path}")
    print(f"数据: {json_path}")

    if args.compare:
        comp = Path(args.compare)
        if comp.exists():
            print(f"\n对比 {comp.name}: {compare(data, comp)}")
        else:
            print(f"\n对比基线不存在: {comp}")


if __name__ == "__main__":
    main()
