# -*- coding: utf-8 -*-
"""
Eval Runner —— 生成质量基线评估

驱动真实的 ToolCallLoop 生成代码（评估的是真实生成质量，不是 mock），
然后对每个任务跑断言检查器，输出可对比的基线报告。

用法：
    cd backend && PYTHONPATH=. python ../eval/run_eval.py
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --tasks t01 t02   # 只跑指定任务
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --no-preview       # 跳过浏览器验证（CI 快跑）
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --resume eval/results/baseline_xxx.json  # 断点续跑（只重跑失败项）
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --with-memory      # 开启记忆注入（A/B 对照）
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --no-plan          # 关掉规划，退回裸 ToolCallLoop（老口径 A/B）

离线查看（不跑评测、不调 LLM、不连库，只读 eval/runs/）：
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --list-runs        # 列出已有运行
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --print-run 20261008_115347
    cd backend && PYTHONPATH=. python ../eval/run_eval.py --backfill-runs    # 把历史 baseline_*.json 补成运行目录

规划口径说明（2026-10-07 修）：
    本脚本此前**直连 ToolCallLoop**，state["plan"] 恒为 None。后果是三件事静默失效，
    而它们恰好是「缺文件」类失败的全部防线：

      ① 编码提示词里的「## 推荐文件结构 / 实现计划」整段消失；
      ② `_pending_plan_files()` 恒返回 []→ 每轮「进度检查：还差 N 个文件」一条都没发出过
         （21 个任务的提示词里该字样出现 0 次）；
      ③ 迭代预算退化成 max(0,3)+3 = 6 轮（与真实工作量脱钩）。

    实测代价：8/21 个任务连 index.html 都没创建就被预算耗尽，断言第一条就挂。
    现在默认走**真实生产管线** team_leader_node → coder_node，
    它同时带来三样评测此前没有的东西：真实计划、`tool_thinking=disabled`、
    Phase 2 定向补全（批量编码漏建的文件会被逐个补齐）。
    要复现历史口径请显式加 --no-plan。

记忆 A/B 说明：
    默认不开记忆 —— 与历史基线一致（eval 从未走过 requirement_service 的
    monkey-patch 注入路径，历史上跑的就是 memory-off）。加 --with-memory 后
    复刻生产注入模式：任务开始时检索一次 build_memory_block()，缓存后包装
    _build_system_prompt，并挂 loop._memory_block 供 file_coder 复用。
    报告中每条结果带 memory_enabled / memory_block_chars，便于对照。

输出：
    eval/results/baseline_<timestamp>.json   # 完整结果
    eval/results/baseline_<timestamp>.md     # 可读摘要
    eval/results/latest.json -> 上述 json    # 最新软链
    eval/runs/<run_id>/trace.db              # 过程：事件 / 正文 / 明细（SQLite）
    eval/runs/<run_id>/run.json              # 结果摘要（后台评测页与 --print-run 读它）

轨迹改道说明（2026-10-08）：
    上面那个 trace.db 不是「顺手多存一份」—— 评测**不再向运营库写任何轨迹**。
    此前它用 `requirement_id = 题号`（t01→1）直接写进真实需求的轨迹里，导致
    运营后台「全部时间」视图把这些行张冠李戴地显示出来（标题取自 7 月的真实需求）。
    改道靠 TALK2CODE_TRACE_DB 单点生效，见 docs/design/eval-observability.md。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import unicodedata
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

# ---------- 生成管线（延迟导入）----------
#
# 顺序不能颠倒：harness 在导入期就会拉起 config 与 models（engine 在那一刻建好），
# 所以 TALK2CODE_TRACE_DB 必须赶在这些 import **之前**设置。放在模块顶层会让改道
# 永远晚一步 —— 而失效的表现是「评测轨迹又写回了运营库」，现象上极难归因。
# 因此 main() 里先 redirect_trace_db()，再 load_pipeline()。
# 见 docs/design/eval-observability.md。
WorkspaceFS = None
AgentState = None
create_tool_registry = None
create_default_hook_manager = None
ToolCallLoop = None

_pipeline_loaded = False


def load_pipeline():
    """导入生成管线（幂等）。正常路径上必须在 `redirect_trace_db()` 之后调用。

    未改道时调用不会报错，只是轨迹会跟随主库 —— 那是给「直接调 run_one_task」
    的调用方留的降级路径，不是推荐用法。
    """
    global WorkspaceFS, AgentState, create_tool_registry
    global create_default_hook_manager, ToolCallLoop, _pipeline_loaded
    if _pipeline_loaded:
        return
    from harness.state.workspace import WorkspaceFS as _ws
    from harness.state.agent_state import AgentState as _st
    from harness.tools.registry import create_tool_registry as _mk_tools
    from harness.constraints.hooks import create_default_hook_manager as _mk_hooks
    from harness.runtime import ToolCallLoop as _loop

    WorkspaceFS, AgentState = _ws, _st
    create_tool_registry, create_default_hook_manager = _mk_tools, _mk_hooks
    ToolCallLoop = _loop
    _pipeline_loaded = True


# ---------- 运行目录 ----------
#
# 一次评测 = 一个自包含目录。轨迹写进它的 SQLite，运营库零写入。
#
#    eval/runs/<run_id>/trace.db      过程：agent_events / blobs / payloads
#    eval/runs/<run_id>/progress.json 过程中间态：已跑完题的结论（跑完即删）
#    eval/runs/<run_id>/run.json      结果：元信息 + 逐题摘要
#
# 保留最近 KEEP_RUNS 次（每次约一个库文件的量级），更早的整目录清掉。
# 注意正在跑的那次也占一个名额（目录已经建好了）。
_RUNS_DIR = _HERE / "runs"
KEEP_RUNS = 5

# run_id 只允许这套字符，且必须与后端 routes/admin_evals.py 的白名单**一致**：
# 补齐历史时若造出带空格/中文的目录名，后台会因为白名单不匹配而直接 404 ——
# 文件明明在，页面上却「运行不存在」，是最难归因的一类。
_RUN_ID_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def new_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def redirect_trace_db(run_id: str) -> Path:
    """建运行目录并把轨迹改道到它的 SQLite。返回该目录。"""
    run_dir = _RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TALK2CODE_TRACE_DB"] = f"sqlite:///{run_dir / 'trace.db'}"
    return run_dir


def prune_runs(keep: int = KEEP_RUNS) -> list:
    """只保留最近 keep 次运行，返回被清掉的 run_id 列表。

    运行目录是生成态（已在 .gitignore 里），不清的话每跑一次多一个库文件。
    目录名是 `%Y%m%d_%H%M%S`，字典序即时间序，不需要读文件内容。
    """
    if keep <= 0 or not _RUNS_DIR.exists():
        return []
    dirs = sorted((d for d in _RUNS_DIR.iterdir() if d.is_dir()), key=lambda d: d.name)
    stale = dirs[:-keep]
    for d in stale:
        shutil.rmtree(d, ignore_errors=True)
    return [d.name for d in stale]


def write_run_manifest(run_dir: Path, *, args, started_at: float,
                       results: list, report_path=None,
                       compare_diff=None) -> Path:
    """写 run.json —— 「结果」侧的唯一数据源（列表页只读它）。

    为什么不复用 report.json：那里是完整报告（含每条断言细节与失败归因），
    面向人读与历史对比；run.json 只装页面要渲染的字段，读起来是纯文件解析，
    不必为了显示「21/21、耗时、成本」去开 21 个库文件。
    """
    passed = sum(1 for r in results if r.passed)
    manifest = {
        "run_id": run_dir.name,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_at)),
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_s": round(time.time() - started_at, 1),
        "model": _resolve_model_name(),
        "args": {
            "with_plan": bool(getattr(args, "with_plan", True)),
            "with_memory": bool(getattr(args, "with_memory", False)),
            "no_preview": bool(getattr(args, "no_preview", False)),
            "tasks": list(getattr(args, "tasks", None) or []),
        },
        "totals": {
            "total": len(results),
            "passed": passed,
            "pass_rate": round(passed / len(results) * 100, 1) if results else 0.0,
            "duration_s": round(sum(r.duration_s or 0 for r in results), 1),
        },
        "report_path": _rel(report_path) if report_path else "",
        # 全量题数：前端据此把「部分题跑」标出来，避免 2/7 被读成成绩暴跌
        "full_set_size": _eval_set_size(),
        "tasks": [asdict(r) for r in results],
    }
    if compare_diff is not None:
        manifest["compare"] = compare_diff
    path = run_dir / "run.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # 结果正式落盘后进度快照就完成使命了：留着会让「结果侧」有两个来源，
    # 而其中一个还写着「进行中」。清理在这里而不是调用方，是为了让
    # 「progress → run.json」这个交接只有一处实现。
    try:
        (run_dir / "progress.json").unlink(missing_ok=True)
    except Exception:
        pass
    return path


def write_progress(run_dir: Path, *, args, started_at: float, results: list,
                   tasks_total: int) -> None:
    """把**已经跑完的题**增量落盘 —— 运行过程中就能看到真实成绩。

    为什么需要它：`run.json` 是跑完才写的，一次全量评测要 60~70 分钟。这期间
    页面只能从过程库反推出「跑到第几题」，而每题过没过无从得知 —— 于是所有
    已开始的题都被含糊地标成「进行中」，看不出「串行推进、逐题出结论」这件事。

    这里只把已经算出来的 TaskResult 提前写一份，不改任何判据：`run.json` 仍是
    结果侧的唯一数据源，`progress.json` 只在它还不存在时被读到。

    原子替换：后台随时可能在读，写到一半被读到会解析失败。
    """
    payload = {
        "run_id": run_dir.name,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_at)),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": _resolve_model_name(),
        "args": {
            "with_plan": bool(getattr(args, "with_plan", True)),
            "with_memory": bool(getattr(args, "with_memory", False)),
            "no_preview": bool(getattr(args, "no_preview", False)),
            "tasks": list(getattr(args, "tasks", None) or []),
        },
        # 本次要跑几题：与 full_set_size 分开 —— 显式 --tasks 时两者不相等
        "tasks_total": tasks_total,
        "tasks": [asdict(r) for r in results],
    }
    tmp = run_dir / "progress.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, run_dir / "progress.json")


def _rel(path) -> str:
    """仓库内相对路径（更可移植），不在仓库内则原样返回。"""
    try:
        return str(Path(path).resolve().relative_to(_REPO))
    except Exception:
        return str(path)


_FULL_SET_CACHE = None


def _eval_set_size() -> int:
    """当前评测集的全量题数（读 tasks.yaml，不写死 —— 题数会变）。

    用来判定一次运行是不是「部分题跑」。不判定的话，调试期的 `1/2` 会和
    正式全量的 `21/21` 在同一个列表里按百分比并排，看着像成绩腰斩。
    """
    global _FULL_SET_CACHE
    if _FULL_SET_CACHE is None:
        try:
            data = yaml.safe_load((_HERE / "tasks" / "tasks.yaml").read_text())
            _FULL_SET_CACHE = len(data.get("tasks") or [])
        except Exception:
            _FULL_SET_CACHE = 0
    return _FULL_SET_CACHE


def _resolve_model_name() -> str:
    try:
        from llm.client import get_client
        return str(getattr(get_client(), "model", "") or "")
    except Exception:
        return ""


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
    memory_enabled: bool = False      # 生成该结果时是否开启记忆注入
    memory_block_chars: int = 0       # 实际注入的记忆块长度（0 = 没检索到东西）
    # ---- 规划口径诊断（2026-10-07）----
    plan_used: bool = False           # 是否跑通了真实 TeamLeader 规划
    plan_files: list = field(default_factory=list)   # 计划声明的文件清单
    plan_error: str = ""              # 规划失败原因（空 = 成功）
    rounds: int = 0                   # 实际消耗的编码轮数（LLM 迭代次数）
    tool_sequence: list = field(default_factory=list)  # 每轮工具名，如 "read_file+write_file"
    workspace: str = ""               # 失败时保留的产物目录（成功/关闭保留时为空）


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
        - 查 ``.css`` 时还要算上**内联样式**：HTML 里 ``<style>`` 块的内容一并纳入。
          单文件方案（index.html + inline style）是合法交付，页面照样有样式。
          2026-10-07 实测 t01：plan 选了单文件，页面用 ``--grad-cool`` 实现了渐变背景、
          预览零错误，却因为工作区里没有 ``.css`` 文件被判 ``content_contains`` 失败。
          这与上面「同扩展名回退」是同一类假阴性 —— **文件布局不该决定断言成败**。
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
        if ext == ".css":
            for f in self.ws.list():
                if not f.lower().endswith((".html", ".htm")):
                    continue
                try:
                    html = self.ws.read(f)
                except Exception:
                    continue
                parts.extend(re.findall(
                    r"<style[^>]*>(.*?)</style>", html, re.IGNORECASE | re.DOTALL
                ))
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
        """轻量选择器匹配（不引浏览器，覆盖 eval 用例）。

        支持：标签名 / `.class` / `#id` / `[class*=x]` / **逗号并集**。

        并集是为「同一个语义有多种合法写法」准备的 —— 例如「CTA 按钮」既可以
        是 `<button>`，也可以是 `<a class="btn" href="#cta">`（着陆页更常见的
        写法，语义上也没错）。此前只认标签名 `button`，把后者判成"未匹配"，
        是一条与产物质量无关的假阴性（2026-10-07 评测 t02 实测：整页 4 条断言
        里 3 条通过，唯独卡在字面 `<button>`）。
        """
        import re
        sel = selector.strip()
        if "," in sel:
            return any(AssertionChecker._selector_match(html, part)
                       for part in sel.split(","))
        if sel.startswith("."):
            return bool(re.search(rf'class\s*=\s*"[^"]*\b{re.escape(sel[1:])}\b', html))
        if sel.startswith("#"):
            return bool(re.search(rf'id\s*=\s*"{re.escape(sel[1:])}"', html))
        m = re.match(r"\[class\*=\s*['\"]?([\w-]+)['\"]?\]$", sel)
        if m:
            # class 属性里出现过该子串即可（btn--sm / cta-btn / button 都算「像按钮」）
            return bool(re.search(rf'class\s*=\s*"[^"]*{re.escape(m.group(1))}', html))
        # 标签名
        return bool(re.search(rf"<{re.escape(sel)}[\s>]", html, re.IGNORECASE))


# ---------- 记忆注入（A/B 开关） ----------

# eval 侧独立持有 MemoryManager 单例：不导入 requirement_service（那会连带
# 拉起 SSE 管理器等一堆服务模块），只复用其注入模式 —— 与生产
# requirement_service._build_injected_memory_block 完全等价的 try/except 包装。
_eval_memory_mgr = None


def _ensure_hits_table():
    """确保 memory_hits 记账表存在。

    eval 是独立进程，不会跑 init_db()（那是应用启动时的事）。表不存在时
    记账会静默降级 —— 注入照常工作，但归因数据全丢，等跑完 20 题才发现
    白跑就晚了。这里提前建一次，代价只是一次 checkfirst 的 DDL 检查。
    """
    try:
        from models import engine, MemoryHit
        MemoryHit.__table__.create(bind=engine, checkfirst=True)
    except Exception as e:
        print(f"[memory] 记账表检查失败（注入照常，但归因数据可能丢失）: {e}", flush=True)


def _get_eval_memory_manager():
    global _eval_memory_mgr
    if _eval_memory_mgr is None:
        _ensure_hits_table()
        from harness.state.memory import MemoryManager
        from llm.client import get_client
        _eval_memory_mgr = MemoryManager(llm_client=get_client())
    return _eval_memory_mgr


def _inject_memory(loop: ToolCallLoop, requirement_content: str, user_id: int,
                   requirement_id: Optional[int] = None,
                   run_id: Optional[str] = None) -> tuple[str, list[int]]:
    """开启记忆时调用：检索一次并包装 _build_system_prompt（与生产同模式）。

    返回 (注入块文本, 记账行 id 列表)。空串 = 检索失败或无记忆，降级为无记忆。
    记账行 id 用于任务结束后回填结果（注入即记账），让每条记忆可归因。
    任何异常都不允许阻断 eval 主流程。
    """
    try:
        block, hit_ids = _get_eval_memory_manager().inject_with_receipt(
            requirement_content, user_id,
            requirement_id=requirement_id, run_id=run_id)
    except Exception as e:
        print(f"[memory] 检索失败，本任务降级为无记忆: {e}", flush=True)
        return "", []
    _original_builder = loop._build_system_prompt
    # 挂到 loop 上供 file_coder 的逐文件编码阶段复用（与生产一致）
    loop._memory_block = block

    def _memory_aware_prompt(state):
        base = _original_builder(state)
        return base + block if block else base

    loop._build_system_prompt = _memory_aware_prompt
    return block, hit_ids


# ---------- 规划（真实 TeamLeader） ----------

def plan_for_task(task: dict) -> tuple[dict, str]:
    """跑真实 TeamLeader 规划，返回 (节点返回值, 失败原因)。

    为什么值得多花一次 LLM 往返：没有 plan，编码阶段的「推荐文件结构」
    「进度检查（还差 N 个文件）」「CompletionContract（Default-FAIL）」
    三件事全部静默失效（见本文件顶部说明）。评测要测的是**产品链路**，
    而产品链路的 Coder 一定有计划。

    澄清门禁的绕法：评测题库只有一句话需求，没有任何「视觉风格」补充，
    TL 的需求确认门禁会拦下来问问题（返回 needs_clarification），
    评测就永远拿不到计划。这里用生产里真实存在的 `[用户补充说明]` 标记放行
    —— 那正是用户填完澄清表单后需求里会带的东西，不是为测试造的旁路。
    """
    from harness.instructions.nodes import team_leader_node

    scratch: AgentState = {
        "requirement_id": int(task["id"].lstrip("t")),
        "user_id": 0,
        "requirement_content": (
            task["requirement"]
            + "\n\n[用户补充说明]\n评测运行：无需澄清，请直接给出实现计划。"
        ),
        "dialogue_history": [],
        "metadata": {},
    }
    try:
        res = team_leader_node(scratch) or {}
    except Exception as e:
        return {}, f"规划异常: {type(e).__name__}: {str(e)[:160]}"

    if res.get("current_step") == "needs_clarification":
        return {}, "TL 要求澄清（放行标记未生效）"
    plan = res.get("plan") or {}
    if not plan or not (plan.get("file_structure") or res.get("implementation_order")):
        return {}, (f"规划未产出可用计划: step={res.get('current_step')} "
                    f"err={str(res.get('error'))[:120]}")
    return res, ""


# ---------- 单任务执行 ----------

def run_one_task(task: dict, args) -> TaskResult:
    """驱动 ToolCallLoop 生成一个任务，然后检查断言"""
    load_pipeline()   # 防御：绕过 main() 直接调用本函数时也要有管线可用
    tid = task["id"]
    result = TaskResult(id=tid, name=task["name"], level=task["level"], passed=False)
    t0 = time.time()

    # 工作区隔离：每次 run 用唯一目录，避免并发/重跑互相覆盖污染结果
    run_id = getattr(args, "run_id", time.strftime("%Y%m%d_%H%M%S"))
    eval_base = Path("/tmp") / "talk2code_eval_runs" / run_id
    ws = WorkspaceFS(user_id=0, requirement_id=int(tid.lstrip("t")), base_dir=eval_base)
    if ws.path.exists():
        shutil.rmtree(ws.path)
    ws.init([])  # 空工作区

    tools = create_tool_registry()
    hooks = create_default_hook_manager()
    loop = ToolCallLoop(workspace=ws, git=None, tools=tools, hooks=hooks)

    # 记忆 A/B：默认关（与历史基线一致）；--with-memory 时走生产注入模式
    mem_hit_ids: list[int] = []
    if getattr(args, "with_memory", False):
        mem_user = getattr(args, "memory_user", 0) or 0
        result.memory_enabled = True
        block, mem_hit_ids = _inject_memory(
            loop, task["requirement"], mem_user,
            requirement_id=int(tid.lstrip("t")), run_id=run_id)
        result.memory_block_chars = len(block)

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
        "metadata": {
            # 生产里由 harness_context 注入（metadata 双路径）；eval 是独立进程，
            # 不注入的话 coder_node 找不到 ToolCallLoop / Workspace。
            "_tool_loop": loop,
            "_workspace": ws,
        },
        "tool_call_count": 0,
        "no_progress_count": 0,
        "last_file_list": [],
        "hook_failures": {},
        "visual_style": None,
    }

    # ---- 阶段 1：规划（真实 TeamLeader）----
    # 规划失败不把整个任务判死：退回旧的「裸 ToolCallLoop」口径继续跑，
    # 但把失败原因记进结果，避免下一次又靠翻日志才发现计划整段是空的。
    if getattr(args, "with_plan", True):
        pres, plan_err = plan_for_task(task)
        if pres.get("plan"):
            state["plan"] = pres["plan"]
            for key in ("tasks", "interfaces", "implementation_order"):
                if pres.get(key) is not None:
                    state[key] = pres[key]
            pm = dict(pres.get("metadata") or {})
            pm.pop("_tool_loop", None)
            pm.pop("_workspace", None)
            state["metadata"].update(pm)
            state["dialogue_history"] = list(pres.get("dialogue_history") or [])
            result.plan_used = True
            result.plan_files = list(
                pres.get("implementation_order")
                or (pres["plan"].get("file_structure") or [])
            )
        else:
            result.plan_error = plan_err
            print(f"    ⚠️ 规划失败，退回裸编码口径: {plan_err}", flush=True)

    # ---- 阶段 2：编码（计划到位时走真实 coder_node）----
    # coder_node 与裸 loop.run 的差别不是「多一层包装」，而是三件实测有效的事：
    #   ① metadata["tool_thinking"]="disabled"（裸跑会带着思考模式烧完额度）
    #   ② CompletionContract 按 implementation_order 初始化（Default-FAIL 硬约束）
    #   ③ Phase 2 定向补全：批量编码漏建的文件会被逐个补齐
    try:
        if result.plan_used:
            from harness.instructions.nodes import coder_node
            # coder_node 返回的是**局部增量**（LangGraph 语义），不是完整 state：
            # 错误必须从返回值读，读 state 会拿到 None 而把失败静默成成功。
            coder_out = coder_node(state) or {}
            final_state = state
            if coder_out.get("error"):
                result.error = str(coder_out["error"])
        else:
            final_state = loop.run(state)
            if final_state.get("error"):
                result.error = str(final_state["error"])
    except Exception as e:
        import traceback
        result.error = f"生成异常: {e}\n{traceback.format_exc()}"

    # ---- 诊断：实际用了多少轮、每轮都干了什么 ----
    # 失败归因全靠它：没有这两个字段，「预算被记账吃光」这种结论只能靠翻日志猜。
    _iter_msgs = [
        m for m in (final_state.get("dialogue_history") or [])
        if isinstance(m, dict) and m.get("role") == "iteration_batch"
    ]
    result.rounds = len(_iter_msgs)
    result.tool_sequence = [
        "+".join(str(t.get("name", "?")) for t in (m.get("tools") or []))
        for m in _iter_msgs
    ]

    result.files = ws.list()
    result.duration_s = round(time.time() - t0, 1)

    # 断言检查
    checker = AssertionChecker(ws, run_preview=not args.no_preview)
    for a in task.get("assertions", []):
        result.assertions.append(asdict(checker.check(a)))

    result.passed = all(ar["passed"] for ar in result.assertions) and not result.error

    # 记账回填：把这个任务的结果写回本次注入的每一行记账记录。
    # 有了它，每条记忆都能算出"被注入 N 次、其中 M 次任务通过"。
    if mem_hit_ids:
        try:
            _get_eval_memory_manager().resolve_hits(mem_hit_ids, result.passed)
        except Exception as e:
            print(f"[memory] 记账回填失败（不影响结果）: {e}", flush=True)

    # 清理临时工作区。
    # 失败时默认保留一份（--no-keep-failed 可关掉）：报告里的 detail 只有一行，
    # 而「为什么挂」常常要看产物本身（几行、差哪个词、引用了什么）。
    # 此外 llm_traffic.log 的 body 超 8000 字符会截断，被截断的那次写入离线重放不出来，
    # 只有留在磁盘上的产物是完整的真相。
    result.workspace = str(ws.path)
    if result.passed or getattr(args, "keep_failed", True) is False:
        try:
            shutil.rmtree(ws.path)
        except Exception:
            pass
        result.workspace = ""
    else:
        print(f"    （失败产物保留在 {ws.path}）", flush=True)
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
        "memory_enabled": any(r.memory_enabled for r in results),
        "by_level": by_level,
        "results": [asdict(r) for r in results],
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    # Markdown 摘要
    mem_note = []
    if data["memory_enabled"]:
        injected = sum(1 for r in results if r.memory_block_chars > 0)
        mem_note = [f"- **记忆注入**: 开启（{injected}/{len(results)} 个任务实际检索到内容）", ""]
    lines = [
        f"# Eval 基线报告 ({ts})",
        "",
        f"- **通过率**: {passed}/{len(results)} ({data['pass_rate']}%)",
        f"- **任务数**: {len(results)}",
        *mem_note,
        "",
        "## 按难度",
        "",
        "| 难度 | 通过 | 总数 |",
        "|---|---|---|",
    ]
    for lv in sorted(by_level):
        lines.append(f"| L{lv} | {by_level[lv]['pass']} | {by_level[lv]['total']} |")
    lines += ["", "## 明细", "",
              "| ID | 名称 | 通过 | 耗时 | 计划 | 轮次 | 失败项 |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        fails = [a["type"] for a in r.assertions if not a["passed"]]
        mark = "✅" if r.passed else "❌"
        plan_cell = f"{len(r.plan_files)}文件" if r.plan_used else "无"
        lines.append(
            f"| {r.id} | {r.name} | {mark} | {r.duration_s}s | "
            f"{plan_cell} | {r.rounds} | {', '.join(fails) or '-'} |"
        )

    # 失败归因：每轮的工具序列。没有它，「预算被记账吃光 / 入口从未被创建」
    # 这类结论只能靠事后翻 llm_traffic.log 猜（且日志截断，未必看得出）。
    fails = [r for r in results if not r.passed]
    if fails:
        lines += ["", "## 失败归因（每轮工具序列）", ""]
        for r in fails:
            lines.append(f"### {r.id} {r.name}")
            if r.plan_error:
                lines.append(f"- ⚠️ 规划失败：{r.plan_error}")
            if r.plan_used:
                lines.append(f"- 计划文件：{', '.join(r.plan_files) or '-'}")
            lines.append(f"- 共 {r.rounds} 轮，产物 {len(r.files)} 个：{', '.join(r.files) or '-'}")
            for i, seq in enumerate(r.tool_sequence, 1):
                lines.append(f"  {i}. {seq}")
            for a in r.assertions:
                if not a["passed"]:
                    lines.append(f"- ❌ {a['type']}: {a['detail'][:160]}")
            if r.workspace:
                lines.append(f"- 产物目录（可直接复看）：`{r.workspace}`")
            lines.append("")

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


def compare_diff(latest_payload: dict, baseline_path: Path):
    """结构化对比 —— 落进 run.json，供页面标出「从过变挂」。

    与 `compare()` 的分工：那个返回一行给人读的字符串（终端用），这个返回结构
    （文件用）。两者读的是同一份 results，不各算一套口径。

    只在**两边都出现过的题**上判定改善/回归：基线里没有的题（新加的用例）既不算
    改善也不算回归，单独列进 added_ids。否则「加了 3 道题、全挂」会被算成
    「0 改善 0 回归」，看起来像什么都没变。
    """
    try:
        old = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️ 基线读取失败，跳过对比: {baseline_path}: {e}")
        return None

    old_rows = {r["id"]: r for r in old.get("results", [])}
    new_rows = {r["id"]: r for r in latest_payload.get("results", [])}

    improved, regressed, changed = [], [], []
    for tid, new in new_rows.items():
        old_row = old_rows.get(tid)
        if old_row is None:
            continue
        before, after = bool(old_row.get("passed")), bool(new.get("passed"))
        if before == after:
            continue
        (improved if after else regressed).append(tid)
        changed.append({
            "id": tid, "name": new.get("name", ""),
            "before": before, "after": after,
            "error": (new.get("error") or "")[:200],
        })

    return {
        "baseline_path": _rel(baseline_path),
        # 基线的自身标识：优先它报告里的 timestamp（与 run_id 同格式），
        # 退而用文件名 —— 页面上要显示「对比基线 XXX」，空着就没法回溯。
        "baseline_run": str(old.get("timestamp") or baseline_path.stem),
        "baseline_pass_rate": old.get("pass_rate"),
        "pass_rate": latest_payload.get("pass_rate"),
        "improved": len(improved), "regressed": len(regressed),
        "improved_ids": improved, "regressed_ids": regressed,
        "added_ids": sorted(set(new_rows) - set(old_rows)),
        "missing_ids": sorted(set(old_rows) - set(new_rows)),
        "changed": changed,
    }


# ---------- 主 ----------

def _preflight_llm() -> tuple:
    """开跑前的 LLM 自检：一次最小调用，验证鉴权 / 额度 / 连通性。

    为什么值得单独做一次（而不是"反正第一个任务会暴露"）：
      21 个任务全量跑一次要 20+ 分钟。如果是 Key 失效、套餐被禁、额度打满，
      这 20 分钟会产出 21 个"失败"，而每一个都跟被测代码无关——既浪费配额，
      又会让人得出"改动后质量下降"的错误结论。

    Returns:
        (ok: bool, detail: str)
    """
    try:
        from llm.client import get_client, key_fingerprint
    except Exception as e:
        return False, f"  无法导入 LLM 客户端: {e}"

    try:
        client = get_client()
    except Exception as e:
        return False, f"  创建 LLM 客户端失败（多为 LLM_API_KEY 未配置）: {e}"

    head = (f"  端点 {getattr(client, 'base_url', '?')} | 模型 {getattr(client, 'model', '?')} "
            f"| Key {key_fingerprint(getattr(client, 'api_key', ''))}")

    t0 = time.time()
    try:
        resp = client.chat(
            prompt="回复一个字：好",
            use_memory=False,
            max_tokens=16,
            timeout=30,
        )
    except Exception as e:
        return False, f"{head}\n  调用异常：{type(e).__name__}: {str(e)[:200]}"

    elapsed = time.time() - t0
    content = getattr(resp, 'content', '') or ''
    if getattr(resp, 'is_error', False) or not content:
        err = getattr(resp, 'error', None) or content or "(空响应)"
        return False, (
            f"{head}\n  调用未返回有效内容：{str(err)[:300]}\n"
            f"  → 若是鉴权/限流，请先修好配置再跑评测，否则 21 个任务会全数假失败。"
        )
    return True, f"{head}\n  往返 {elapsed:.2f}s，返回 {content.strip()[:20]!r}"


def main():
    parser = argparse.ArgumentParser(description="Talk2Code 生成质量 Eval")
    parser.add_argument("--tasks", nargs="*", help="只跑指定任务 id（如 t01 t02）")
    parser.add_argument("--no-preview", action="store_true", help="跳过浏览器验证（快）")
    parser.add_argument("--compare", metavar="BASELINE_JSON", help="对比历史基线")
    parser.add_argument("--resume", metavar="BASELINE_JSON",
                        help="断点续跑：复用该报告中已 PASS 的任务结果，只重跑未通过的")
    parser.add_argument("--with-memory", action="store_true",
                        help="开启记忆注入（A/B 对照；默认关闭，与历史基线一致）")
    parser.add_argument("--memory-user", type=int, default=0, metavar="USER_ID",
                        help="记忆检索使用的 user_id（默认 0，即 eval 专用用户）")
    parser.add_argument("--delay", type=float, default=0.0, metavar="SECONDS",
                        help="任务之间的间隔秒数（默认 0）。免费额度账号建议 ≥10——"
                             "评测是密集批量调用，不节流会把自己的配额打满，"
                             "429/401 造成的失败会被误读成代码问题")
    parser.add_argument("--no-preflight", action="store_true",
                        help="跳过开跑前的 LLM 连通性/鉴权自检（默认会自检一次）")
    parser.add_argument("--no-keep-failed", dest="keep_failed", action="store_false",
                        default=True,
                        help="失败任务的工作区照旧删掉（默认保留，便于离线复看产物）")
    parser.add_argument("--with-plan", dest="with_plan", action="store_true", default=True,
                        help="走真实 TeamLeader 规划 + coder_node（默认，见文件顶部说明）")
    parser.add_argument("--no-plan", dest="with_plan", action="store_false",
                        help="关掉规划，退回裸 ToolCallLoop（历史口径 A/B）")
    parser.add_argument("--run-id", dest="run_id", default=None, metavar="ID",
                        help="本次运行的标识（默认时间戳）。决定 eval/runs/<run_id>/ "
                             "这个自包含目录的名字，也是后台查看评测过程的键")
    # ---- 离线子命令（不跑评测，只读 eval/runs/）----
    parser.add_argument("--print-run", dest="print_run", default=None, metavar="RUN_ID",
                        help="打印某次运行的结果摘要（纯文本，不起后端、不调 LLM）")
    parser.add_argument("--list-runs", dest="list_runs", action="store_true",
                        help="列出已有的运行目录")
    parser.add_argument("--backfill-runs", dest="backfill_runs", action="store_true",
                        help="把历史 eval/results/baseline_*.json 补成 eval/runs/<ts>/run.json")
    args = parser.parse_args()

    # 运行标识：目录名即时间序，prune_runs 依赖它排序
    args.run_id = args.run_id or new_run_id()

    # 离线子命令：只读 eval/runs/ 下的文件，不起管线、不调 LLM、不连库。
    # 必须放在 redirect_trace_db() 之前 —— 那一步会 mkdir 出一个空运行目录。
    if args.print_run is not None or args.list_runs or args.backfill_runs:
        if args.backfill_runs:
            backfill_runs()
        if args.list_runs:
            list_runs()
        if args.print_run is not None:
            sys.exit(print_run(args.print_run))
        sys.exit(0)

    # 改道必须早于 load_pipeline()：harness 在导入期就建好了 models 引擎，
    # 晚一步设置 TALK2CODE_TRACE_DB 就完全无效（见文件顶部说明）。
    run_dir = redirect_trace_db(args.run_id)
    load_pipeline()
    started_at = time.time()

    tasks = yaml.safe_load((_HERE / "tasks" / "tasks.yaml").read_text())["tasks"]
    if args.tasks:
        want = set(args.tasks)
        tasks = [t for t in tasks if t["id"] in want]
        if not tasks:
            print(f"未找到任务: {args.tasks}")
            sys.exit(1)

    # 断点续跑：加载历史结果，已 PASS 的直接复用，不重跑
    resume_map = {}
    if args.resume:
        try:
            rd = json.loads(Path(args.resume).read_text())
            resume_map = {r["id"]: r for r in rd.get("results", [])}
            print(f"断点续跑：已加载 {len(resume_map)} 个历史结果\n")
        except Exception as e:
            print(f"resume 读取失败，忽略: {e}")

    mem_status = f"on(user={args.memory_user})" if args.with_memory else "off"
    plan_status = "on(team_leader+coder_node)" if args.with_plan else "off(裸 ToolCallLoop)"
    print(f"Eval: {len(tasks)} 个任务 (preview={'off' if args.no_preview else 'on'}, "
          f"memory={mem_status}, plan={plan_status})\n")

    # 开跑前自检：Key 失效/套餐被禁/额度打满时，21 个任务会全部"失败"，
    # 而失败原因跟被测代码毫无关系——必须在花掉 20 分钟之前就拦住。
    if not args.no_preflight:
        ok, detail = _preflight_llm()
        print(f"LLM 自检: {'✅ 通过' if ok else '❌ 失败'}\n{detail}\n")
        if not ok:
            print("自检未通过，已中止评测（避免把配置问题误判成用例失败）。"
                  "确认修复后可加 --no-preflight 强制开跑。")
            sys.exit(2)

    results = []

    def snap():
        """每题跑完立刻落一份进度快照 —— 页面据此看到逐题结论，而不是一片「进行中」。

        快照写失败绝不能影响评测本身：它是给旁路看的，不是结果。
        """
        try:
            write_progress(run_dir, args=args, started_at=started_at,
                           results=results, tasks_total=len(tasks))
        except Exception as e:
            print(f"    （进度快照写入失败，不影响评测: {e}）", flush=True)

    for i, task in enumerate(tasks, 1):
        rid = task["id"]
        # 断点续跑：已 PASS 的任务直接复用结果，跳过生成（省配额、省时）
        if rid in resume_map and resume_map[rid].get("passed"):
            old = resume_map[rid]
            r = TaskResult(
                id=rid, name=old["name"], level=old.get("level"),
                passed=True, error=old.get("error"),
                duration_s=old.get("duration_s"),
                files=old.get("files", []),
                assertions=old.get("assertions", []),
                memory_enabled=old.get("memory_enabled", False),
                memory_block_chars=old.get("memory_block_chars", 0),
                plan_used=old.get("plan_used", False),
                plan_files=old.get("plan_files", []),
                plan_error=old.get("plan_error", ""),
                rounds=old.get("rounds", 0),
                tool_sequence=old.get("tool_sequence", []),
                workspace=old.get("workspace", ""),
            )
            print(f"[{i}/{len(tasks)}] {rid} {task['name']} ... ⏭️ (resume PASS)")
            results.append(r)
            snap()
            continue

        print(f"[{i}/{len(tasks)}] {rid} {task['name']} ... ", end="", flush=True)
        try:
            r = run_one_task(task, args)
        except Exception as e:
            import traceback
            r = TaskResult(
                id=rid, name=task["name"], level=task.get("level"),
                passed=False, error=f"任务级未捕获异常: {e}\n{traceback.format_exc()}",
            )
        # LLM 读超时（agnes 偶发抖动）自动重试一次：基础设施问题，非质量缺陷
        if (not r.passed) and r.error and ("timed out" in r.error.lower()):
            print("↻ LLM 超时，自动重试 1 次... ", end="", flush=True)
            try:
                r = run_one_task(task, args)
            except Exception as e:
                import traceback
                r = TaskResult(
                    id=rid, name=task["name"], level=task.get("level"),
                    passed=False, error=f"任务级未捕获异常: {e}\n{traceback.format_exc()}",
                )
        mark = "✅" if r.passed else "❌"
        mem_tag = f" [mem:{r.memory_block_chars}c]" if r.memory_enabled else ""
        print(f"{mark} ({r.duration_s}s){mem_tag}" + (f"  {r.error}" if r.error else ""))
        results.append(r)
        snap()
        # 任务间节流：评测是密集批量调用，全速跑会把免费额度打满（实测 16 个并发
        # 请求即触发 429），之后的失败全是配额问题而非代码问题。
        if args.delay and i < len(tasks):
            print(f"    节流：等待 {args.delay:.0f}s ...", flush=True)
            time.sleep(args.delay)

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

    # 运行目录：过程（trace.db，此时已全部落盘）与结果摘要（run.json）。
    # 这两样都不在运营库里 —— 评测对运营库的写入在结构上被切断了。
    # 对比结果一并写进去：页面要标「从过变挂」，靠的就是这一份结构化 diff。
    cdiff = compare_diff(data, Path(args.compare)) if args.compare else None
    write_run_manifest(
        run_dir, args=args, started_at=started_at, results=results,
        report_path=json_path, compare_diff=cdiff,
    )
    # 结果已正式落盘，进度快照由 write_run_manifest 顺手清掉（交接只有一处实现）
    print(f"运行目录: {_rel(run_dir)}   （过程库 {_rel(run_dir / 'trace.db')}）")

    removed = prune_runs()
    if removed:
        print(f"已清理较早的 {len(removed)} 次运行: {', '.join(removed)}")


# ---------- 离线查看 / 历史补齐 ----------

def _pad(text: str, width: int) -> str:
    """按**显示宽度**右补空格（中日韩字符占 2 列）。

    用 `f"{s:24s}"` 会按字符数补：中文任务名一个字符占两格，整列就错开了，
    一眼看上去像排版坏了 —— 终端输出只有「对齐」这一个可读性手段。
    """
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(0, width - w)


def print_run(run_id: str) -> int:
    """把一次运行的结果摘要打到终端。不碰 LLM、不连数据库。

    存在的理由：只想确认「上一轮跑成什么样」时，不必起后端、开浏览器。
    数据源就是 run.json —— 与页面读的是同一个文件，不会出现「命令行看到的和
    页面上不一样」。
    """
    run_dir = _RUNS_DIR / run_id
    manifest = run_dir / "run.json"
    if not manifest.exists():
        print(f"运行不存在（缺少 {_rel(manifest)}）。可用 --list-runs 查看已有运行。")
        return 1

    man = json.loads(manifest.read_text(encoding="utf-8"))
    totals = man.get("totals") or {}
    print(f"运行 {man.get('run_id') or run_id}")
    print(f"  时间    {man.get('started_at')} → {man.get('finished_at')} "
          f"（{man.get('duration_s')}s）")
    print(f"  模型    {man.get('model') or '—'}")
    args = man.get("args") or {}
    print(f"  配置    plan={args.get('with_plan')} memory={args.get('with_memory')} "
          f"preview={'off' if args.get('no_preview') else 'on'}"
          + (f" tasks={args.get('tasks')}" if args.get("tasks") else ""))
    print(f"  通过率  {totals.get('passed')}/{totals.get('total')} "
          f"({totals.get('pass_rate')}%)")
    print(f"  过程库  {'有' if (run_dir / 'trace.db').exists() else '无'}"
          f"   报告 {man.get('report_path') or '—'}")

    cmp_ = man.get("compare")
    if cmp_:
        print(f"  对比    基线 {cmp_.get('baseline_run')}："
              f"改善 {cmp_.get('improved', 0)} 回归 {cmp_.get('regressed', 0)}"
              + (f"  回归题 {cmp_.get('regressed_ids')}"
                 if cmp_.get("regressed_ids") else ""))

    print("\n  逐题：")
    for t in man.get("tasks") or []:
        mark = "✅" if t.get("passed") else "❌"
        fails = [a.get("type") for a in (t.get("assertions") or [])
                 if not a.get("passed")]
        line = (f"    {mark} {t.get('id')} "
                f"{_pad(str(t.get('name'))[:24], 28)}"
                f"{str(t.get('duration_s')):>7}s  {t.get('rounds')} 轮")
        if fails:
            line += f"  未达成: {', '.join(fails)}"
        print(line)
    return 0


def _run_id_to_iso(run_id: str) -> str:
    """`20261007_143036` → `2026-10-07T14:30:36`。

    历史报告里没有起止时刻，只有这个时间戳。原样塞进 started_at 的话前端
    `new Date("20261007_143036")` 解析不出来，列表上那一格就显示成「—」——
    一整列空白比没有这列更糟。run_id 本身即开始时刻，转成 ISO 是等价的。
    """
    if _RUN_ID_SAFE.match(run_id) and len(run_id) == 15 and run_id[8] == "_":
        return (f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}"
                f"T{run_id[9:11]}:{run_id[11:13]}:{run_id[13:15]}")
    return run_id


def backfill_runs(keep: int = KEEP_RUNS) -> list:
    """把历史的 eval/results/baseline_*.json 转成 eval/runs/<ts>/run.json。

    历史运行只有结果、没有过程（那时还没落 trace.db），所以补齐的目录里
    只有 run.json —— 页面会显示「无过程数据」，不会给一个点了没反应的入口。

    两个刻意的约束：
      · **只补最新的 keep 次**。历史文件有三十多份（含大量调试期的单题跑），
        先全部转出来再 prune 等于写 33 份删 28 份 —— 白写一遍，还留下中间态。
      · **幂等**：已存在 run.json 的目录不覆盖 —— 真跑过的运行不能被历史摘要盖掉。

    返回新建的 run_id 列表。
    """
    results_dir = _HERE / "results"
    if not results_dir.is_dir():
        print(f"没有历史结果目录: {_rel(results_dir)}")
        return []

    # 先扫出所有候选，按 run_id（= 时间戳）倒序，只取最近 keep 个
    cands = []
    for jp in results_dir.glob("baseline_*.json"):
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  跳过（解析失败）{jp.name}: {e}")
            continue
        if not data.get("results"):
            continue
        run_id = str(data.get("timestamp") or jp.stem.replace("baseline_", ""))
        if not _RUN_ID_SAFE.match(run_id):
            print(f"  跳过（标识不合法）{jp.name}: run_id={run_id!r}")
            continue
        cands.append((run_id, jp, data))
    cands.sort(key=lambda x: x[0], reverse=True)

    created = []
    for run_id, jp, data in cands[:keep]:
        rows = data["results"]
        run_dir = _RUNS_DIR / run_id
        existing = run_dir / "run.json"
        if existing.exists():
            # 幂等，但**允许刷新自己补出来的**：补齐逻辑本身会演进（新增字段），
            # 只要那份 run.json 是 backfilled 的派生数据就能安全重写。
            # 真跑过的运行绝不能动 —— 它的 trace.db 与逐题细节是原始证据。
            try:
                old_man = json.loads(existing.read_text(encoding="utf-8"))
            except Exception:
                old_man = {}
            if not old_man.get("backfilled"):
                continue
        run_dir.mkdir(parents=True, exist_ok=True)

        totals = {
            "total": data.get("total", len(rows)),
            "passed": data.get("passed",
                               sum(1 for r in rows if r.get("passed"))),
            "pass_rate": data.get("pass_rate"),
            "duration_s": round(sum(r.get("duration_s") or 0 for r in rows), 1),
        }
        # 时间：报告里只有一个 timestamp，用它当开始时刻。历史报告没记起止，
        # 编一个「看起来精确」的区间反而误导。
        #
        # full_set_size：当时评测集的**全量题数**。历史报告里混着大量调试期的
        # 单题/部分跑（`1/2`、`2/7`），若不标出来，「2/7 = 28.6%」和
        # 「21/21 = 100%」在列表里并排显示，会被直接读成成绩掉了一大截。
        man = {
            "run_id": run_id,
            "started_at": _run_id_to_iso(run_id),
            "finished_at": None,
            "duration_s": totals["duration_s"],
            "model": "",
            "args": {"with_plan": True,
                     "with_memory": bool(data.get("memory_enabled")),
                     "no_preview": False, "tasks": []},
            "totals": totals,
            "full_set_size": _eval_set_size(),
            "report_path": _rel(jp),
            "tasks": rows,
            "backfilled": True,
        }
        (run_dir / "run.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(run_id)

    total_cand = len(cands)
    if created:
        print(f"已补齐 {len(created)} 次历史运行: {', '.join(created)}")
    else:
        print("没有需要补齐的历史运行（都已存在或缺 timestamp）")

    if total_cand > keep:
        print(f"（历史结果共 {total_cand} 份，只补最近 {keep} 次 —— "
              f"更早的多为调试期单题跑，没有过程数据可看）")
    removed = prune_runs(keep)
    if removed:
        print(f"已清理较早的 {len(removed)} 次运行: {', '.join(removed)}")
    return created


def list_runs() -> int:
    """列出已有运行目录（含被补齐的历史）。"""
    if not _RUNS_DIR.is_dir():
        print("暂无运行目录。")
        return 0
    dirs = sorted((d for d in _RUNS_DIR.iterdir() if d.is_dir()),
                  key=lambda d: d.name, reverse=True)
    if not dirs:
        print("暂无运行目录。")
        return 0
    for d in dirs:
        man_path = d / "run.json"
        man = {}
        if man_path.exists():
            try:
                man = json.loads(man_path.read_text(encoding="utf-8"))
            except Exception:
                man = {}
        t = man.get("totals") or {}
        tag = " [仅结果]" if man.get("backfilled") else ""
        print(f"  {d.name}  {t.get('passed', '?')}/{t.get('total', '?')}"
              f"  {man.get('duration_s', '?')}s"
              f"  过程库={'有' if (d / 'trace.db').exists() else '无'}{tag}")
    return 0


if __name__ == "__main__":
    main()
