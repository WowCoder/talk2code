# -*- coding: utf-8 -*-
"""验证 eval --with-memory A/B 开关的实现（不真跑 LLM 任务）。

覆盖 6 组断言：
  1. run_eval 模块可导入（backend 路径注入正常）
  2. TaskResult 新增字段存在且默认值正确
  3. _inject_memory 正常路径：包装 _build_system_prompt + 挂 _memory_block
  4. _inject_memory 异常路径：检索失败返回空串，prompt 不被污染
  5. 空块路径：检索到空串时 prompt 原样返回
  6. write_reports 报告带 memory_enabled 字段

由 `tests/unit/test_memory_verification.py` 以子进程方式纳管。
也可单独运行：cd backend && PYTHONPATH=. python tests/memory_verify/verify_eval_memory_ab.py
"""
import sys
from pathlib import Path


def _find_repo_root(start: str) -> Path:
    """从脚本位置向上找到同时含 backend/harness 与 eval/ 的仓库根（迁移后仍稳健）。"""
    for cand in [Path(start).resolve(), *Path(start).resolve().parents]:
        if (cand / "backend" / "harness").is_dir() and (cand / "eval").is_dir():
            return cand
    raise RuntimeError("找不到仓库根目录（需含 backend/harness 与 eval/）")


REPO = _find_repo_root(__file__)
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO / "eval"))

import run_eval  # noqa: E402

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))


print("== 1. 模块导入 ==")
check("import run_eval", hasattr(run_eval, "run_one_task"))

print("== 2. TaskResult 新字段 ==")
r = run_eval.TaskResult(id="t01", name="x", level=1, passed=False)
check("memory_enabled 默认 False", r.memory_enabled is False)
check("memory_block_chars 默认 0", r.memory_block_chars == 0)
d = run_eval.asdict(r)
check("asdict 含新字段", "memory_enabled" in d and "memory_block_chars" in d)

print("== 3. _inject_memory 正常路径 ==")


class FakeLoop:
    def _build_system_prompt(self, state):
        return "BASE"


def _make_loop_with_stub_block(block, hit_ids=None):
    """stub 掉 eval 单例，避免加载检索模型"""
    import types
    stub = types.SimpleNamespace()
    stub.inject_with_receipt = lambda req, uid, requirement_id=None, run_id=None: (
        block, hit_ids if hit_ids is not None else [101, 102])
    run_eval._eval_memory_mgr = stub


loop = FakeLoop()
_make_loop_with_stub_block("MEMBLOCK-123")
blk, hits = run_eval._inject_memory(loop, "做一个贪吃蛇", 0)
check("返回注入块", blk == "MEMBLOCK-123", repr(blk))
check("返回记账行 id", hits == [101, 102], repr(hits))
check("loop._memory_block 已挂载", getattr(loop, "_memory_block", "") == "MEMBLOCK-123")
check("包装后 prompt = BASE+MEM", loop._build_system_prompt({}) == "BASEMEMBLOCK-123")

print("== 4. _inject_memory 异常路径 ==")


class _Boom:
    def inject_with_receipt(self, req, uid, requirement_id=None, run_id=None):
        raise RuntimeError("db down")


run_eval._eval_memory_mgr = _Boom()
loop2 = FakeLoop()
blk2, hits2 = run_eval._inject_memory(loop2, "需求", 0)
check("异常时返回空串", blk2 == "", repr(blk2))
check("异常时返回空 hit_ids", hits2 == [], repr(hits2))
check("异常时不包装 prompt", loop2._build_system_prompt({}) == "BASE")
check("异常时 _memory_block 为空串", getattr(loop2, "_memory_block", "") == "")

print("== 5. 空块路径 ==")
_make_loop_with_stub_block("", hit_ids=[])
loop3 = FakeLoop()
blk3, hits3 = run_eval._inject_memory(loop3, "需求", 0)
check("空块返回空串", blk3 == "")
check("空块时无记账行", hits3 == [], repr(hits3))
check("空块时 prompt 原样", loop3._build_system_prompt({}) == "BASE")

print("== 6. write_reports 带 memory_enabled ==")
import tempfile, shutil  # noqa: E402
results = [
    run_eval.TaskResult(id="t01", name="a", level=1, passed=True,
                        memory_enabled=True, memory_block_chars=800),
    run_eval.TaskResult(id="t02", name="b", level=2, passed=False),
]
# write_reports 写到 _HERE/results/，临时重定向 _HERE 避免污染真实结果目录
tmp_base = Path(tempfile.mkdtemp())          # tmp_base/results/ 会被创建
orig_here = run_eval._HERE
run_eval._HERE = tmp_base
try:
    jp, mp, data = run_eval.write_reports(results, 2)
    check("报告含 memory_enabled=True", data.get("memory_enabled") is True)
    check("结果条目含 memory 字段",
          all("memory_enabled" in it and "memory_block_chars" in it for it in data["results"]))
    md = mp.read_text()
    check("MD 含记忆注入行", "记忆注入" in md and "1/2" in md)
finally:
    run_eval._HERE = orig_here
    shutil.rmtree(tmp_base, ignore_errors=True)

print(f"\n结果: {len(passed)} passed, {len(failed)} failed")
if failed:
    print("失败项:", failed)
    sys.exit(1)
print("ALL PASS")
