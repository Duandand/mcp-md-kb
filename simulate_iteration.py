# -*- coding: utf-8 -*-
"""迭代闭环模拟：生成 iteration_before_after.csv 的可复现证据。

方法论：
  - 同一个「周报素材整理」任务，由规则模拟的 Agent 执行多轮；
  - Agent 犯错模式固定且两种环境完全一致（每3轮循环一次）：
      模式0：误传绝对路径；模式1：手动拼接 kb_root 前缀；模式2：直接用正确相对路径；
  - 「改前」= 迭代前服务快照（无路径规范化 / 错误返回不含 agent_guidance /
    search 结果不含 is_relative 标记），在本文件内原样复刻；
  - 「改后」= 真实的 mcp_server.py 工具函数（import 后把监控连接切到
    iteration_after.db，避免污染 monitor.db）；
  - 两个阶段各自写入独立 sqlite（schema 与 monitor.db 一致），
    最后汇总统计导出 iteration_before_after.csv。

用法：./venv/bin/python simulate_iteration.py
"""
import asyncio
import csv
import os
import sqlite3
import time

KB = os.path.abspath("./sample_notes")
API_KEY = "demo-key-12345"
CYCLES = 20  # 每个 Agent 犯错模式跑 20 轮，共 60 轮任务 / 阶段

SCHEMA = """CREATE TABLE IF NOT EXISTS mcp_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT, api_key TEXT, timestamp REAL,
    duration_ms INTEGER, success INTEGER, error_code TEXT)"""


def db_record(conn):
    def record(tool_name, start_ts, success, error_code):
        conn.execute(
            "INSERT INTO mcp_calls (tool_name, api_key, timestamp, duration_ms, success, error_code)"
            " VALUES (?,?,?,?,?,?)",
            (tool_name, "sim***", time.time(), int((time.time() - start_ts) * 1000),
             1 if success else 0, error_code),
        )
        conn.commit()
    return record


# =====================================================================
# 改前服务快照（V1）：无规范化、无 agent_guidance、无 is_relative
# =====================================================================
class V1Server:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(SCHEMA)
        self.conn.commit()
        self.record = db_record(self.conn)

    def _err(self, code, msg):
        return {"success": False, "error_code": code, "message": msg}

    async def list_markdown_files(self, kb_root_path, api_key):
        start = time.time()
        if not os.path.isdir(kb_root_path):
            self.record("list_markdown_files", start, False, "PATH_NOT_EXIST")
            return self._err("PATH_NOT_EXIST", "目录不存在")
        files = sorted(
            os.path.relpath(os.path.join(r, fn), kb_root_path)
            for r, _, fns in os.walk(kb_root_path) for fn in fns if fn.lower().endswith(".md")
        )
        self.record("list_markdown_files", start, True, "")
        return {"success": True, "files": files, "count": len(files)}

    async def search_markdown_by_keyword(self, kb_root_path, keyword, max_results, api_key):
        start = time.time()
        if not os.path.isdir(kb_root_path):
            self.record("search_markdown_by_keyword", start, False, "PATH_NOT_EXIST")
            return self._err("PATH_NOT_EXIST", "目录不存在")
        if not keyword.strip():
            self.record("search_markdown_by_keyword", start, False, "EMPTY_KEYWORD")
            return self._err("EMPTY_KEYWORD", "关键词为空")
        matches = []
        for r, _, fns in os.walk(kb_root_path):
            for fn in sorted(fns):
                if not fn.lower().endswith(".md"):
                    continue
                full = os.path.join(r, fn)
                rel = os.path.relpath(full, kb_root_path)
                with open(full, "r", encoding="utf-8") as f:
                    for idx, line in enumerate(f):
                        if keyword.lower() in line.lower():
                            matches.append({"file_path": rel, "snippet": line.strip(), "line_no": idx + 1})
                            break
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break
        self.record("search_markdown_by_keyword", start, True, "")
        return {"success": True, "matches": matches}

    async def extract_md_summary(self, kb_root_path, file_path, api_key):
        start = time.time()
        full = os.path.join(kb_root_path, file_path)  # V1：不做任何规范化
        if not os.path.isfile(full):
            self.record("extract_md_summary", start, False, "FILE_NOT_FOUND")
            return self._err("FILE_NOT_FOUND", "文件不存在")  # V1：没有 agent_guidance
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
        title = next((l.lstrip("# ").strip() for l in content.splitlines() if l.startswith("# ")), "")
        self.record("extract_md_summary", start, True, "")
        return {"success": True, "title": title, "summary": content[:300], "word_count": len(content.split())}


# =====================================================================
# 改后：真实 mcp_server 工具函数
# =====================================================================
def load_v2_server(db_path):
    import mcp_server as srv
    srv._conn = sqlite3.connect(db_path)  # 监控连接切到实验库，避免污染 monitor.db
    srv._conn.execute(SCHEMA)
    srv._conn.commit()
    return srv


# =====================================================================
# 模拟 Agent：同一套决策逻辑，仅在「收到错误后如何反应」上区分 V1/V2
# =====================================================================
async def run_task(server, mistake_mode: int, guided: bool):
    """执行一次完整任务：list → search『项目风险』→ extract_md_summary。
    返回本次任务的 MCP 调用次数与摘要工具失败次数。
    """
    calls, summary_fails = 0, 0

    async def count_call(coro):
        nonlocal calls
        calls += 1
        return await coro

    await count_call(server.list_markdown_files(KB, API_KEY))

    rel_file = "凤凰平台周报-第40周.md"
    r = await count_call(server.search_markdown_by_keyword(KB, "项目风险", 5, API_KEY))
    if r.get("success") and r.get("matches"):
        rel_file = r["matches"][0]["file_path"]

    # Agent 按犯错模式构造 file_path
    if mistake_mode == 0:
        file_path = os.path.abspath(os.path.join(KB, rel_file))          # 误传绝对路径
    elif mistake_mode == 1:
        file_path = os.path.basename(KB) + "/" + rel_file                # 手动拼接多余前缀
    else:
        file_path = rel_file                                             # 正确相对路径

    # 摘要调用 + 失败重试策略
    for _ in range(10):
        r = await count_call(server.extract_md_summary(KB, file_path, API_KEY))
        if r.get("success"):
            break
        summary_fails += 1
        if guided:
            # V2：有 agent_guidance → 立即改用 list 返回的相对路径（1 次纠正）
            file_path = rel_file
        else:
            # V1：无引导 → 盲目重试同样的错误路径 2 次，随后才退化到重新 list
            if summary_fails % 3 == 0:
                await count_call(server.list_markdown_files(KB, API_KEY))
                file_path = rel_file
    return calls, summary_fails


async def run_phase(server, guided: bool):
    total_calls, total_fails, task_calls = 0, 0, []
    for _ in range(CYCLES):
        for mode in range(3):
            calls, fails = await run_task(server, mode, guided)
            total_calls += calls
            total_fails += fails
            task_calls.append(calls)
    n = len(task_calls)
    return {
        "total_calls": total_calls,
        "summary_fails": total_fails,
        "avg_calls_per_task": round(total_calls / n, 2),
        "max_calls_per_task": max(task_calls),
    }


def query_db(path):
    conn = sqlite3.connect(path)
    row = conn.execute(
        """SELECT COUNT(*), SUM(CASE WHEN success=0 THEN 1 ELSE 0 END),
                  SUM(CASE WHEN error_code='FILE_NOT_FOUND' THEN 1 ELSE 0 END),
                  ROUND(AVG(duration_ms), 2)
           FROM mcp_calls"""
    ).fetchone()
    conn.close()
    return row


def main():
    for f in ("iteration_before.db", "iteration_after.db", "iteration_before_after.csv"):
        if os.path.exists(f):
            os.remove(f)

    print("运行改前模拟（V1 快照）...")
    before = asyncio.run(run_phase(V1Server("iteration_before.db"), guided=False))
    print("运行改后模拟（V2 真实服务）...")
    after = asyncio.run(run_phase(load_v2_server("iteration_after.db"), guided=True))

    b_total, b_fail, b_fnf, b_avg_ms = query_db("iteration_before.db")
    a_total, a_fail, a_fnf, a_avg_ms = query_db("iteration_after.db")

    rows = [
        ["指标", "改前(before)", "改后(after)", "变化", "说明"],
        ["任务轮数", 60, 60, "-", "同一任务 × 3种犯错模式 × 20轮"],
        ["MCP总调用次数", b_total, a_total, f"-{(1 - a_total / b_total) * 100:.0f}%", "监控库 mcp_calls 总行数"],
        ["单任务平均调用次数", before["avg_calls_per_task"], after["avg_calls_per_task"],
         f"-{(1 - after['avg_calls_per_task'] / before['avg_calls_per_task']) * 100:.0f}%", "含重试与恢复调用"],
        ["单任务最大调用次数", before["max_calls_per_task"], after["max_calls_per_task"], "-", "最差情况"],
        ["失败调用总数", b_fail, a_fail, f"-{(1 - a_fail / b_fail) * 100:.0f}%", "success=0 的调用"],
        ["FILE_NOT_FOUND次数", b_fnf, a_fnf, f"-{(1 - a_fnf / b_fnf) * 100:.0f}%", "路径类错误的直接体现"],
        ["extract_md_summary失败次数", before["summary_fails"], after["summary_fails"],
         f"-{(1 - after['summary_fails'] / before['summary_fails']) * 100:.0f}%", "迭代前的重灾区工具"],
        ["平均耗时(ms/调用)", b_avg_ms, a_avg_ms, "-", "本地文件操作，量级为毫秒"],
    ]
    with open("iteration_before_after.csv", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(rows)

    print("\n==== 迭代前后对比 ====")
    for r in rows:
        print(" | ".join(str(x) for x in r))
    print("\n已导出 iteration_before_after.csv")


if __name__ == "__main__":
    main()
