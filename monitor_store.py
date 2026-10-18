# -*- coding: utf-8 -*-
"""监控存储位置解析与连接管理。

背景：部分办公 Agent（如 Trae）以沙箱方式拉起 MCP 服务器子进程，进程对工作区
目录只读，写 monitor.db 时 sqlite 创建 journal 文件失败，报
`attempt to write a readonly database`（文件本身权限正常）。

策略：
  - 写端（mcp_server 启动时）：逐个候选探测，用第一个**可写**的：
      env MONITOR_DB → ./monitor.db → ~/.md_kb_mcp/monitor.db → 系统临时目录
  - 读端（报表/看板脚本）：优先选**已有数据**的候选——因为查询脚本运行在
    无沙箱的 shell 里，必须自动定位到服务端实际写入的回退路径，避免读到空库
  - 全部失败 → 内存库（监控降级为不持久化，stderr 警告，工具调用不受影响）
"""
import os
import sqlite3
import sys
import tempfile

SCHEMA = """CREATE TABLE IF NOT EXISTS mcp_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT, api_key TEXT, timestamp REAL,
    duration_ms INTEGER, success INTEGER, error_code TEXT)"""


def _candidates():
    items = []
    env = os.getenv("MONITOR_DB")
    if env:
        items.append(("env MONITOR_DB", os.path.abspath(os.path.expanduser(env))))
    items.append(("workspace", os.path.abspath("./monitor.db")))
    items.append(("home", os.path.expanduser("~/.md_kb_mcp/monitor.db")))
    items.append(("tempdir", os.path.join(tempfile.gettempdir(), "md_kb_monitor.db")))
    return items


def _connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    return sqlite3.connect(path)


def _probe_write(path):
    """真实写入探测：建表 → 插入探测行 → 删除 → 提交，任何一步失败即不可写。"""
    try:
        conn = _connect(path)
        conn.execute(SCHEMA)
        conn.execute(
            "INSERT INTO mcp_calls (tool_name, api_key, timestamp, duration_ms, success, error_code)"
            " VALUES ('__probe__', '', 0, 0, 1, '')")
        conn.execute("DELETE FROM mcp_calls WHERE tool_name = '__probe__'")
        conn.commit()
        conn.close()
        return True
    except (sqlite3.OperationalError, OSError):
        return False


def _row_count(path):
    """只读方式统计行数；文件/表不存在返回 -1。"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM mcp_calls LIMIT 1")
        n = conn.execute("SELECT COUNT(*) FROM mcp_calls").fetchone()[0]
        conn.close()
        return n
    except (sqlite3.OperationalError, sqlite3.DatabaseError):
        return -1


def resolve_for_write():
    """服务端启动用：返回 (conn, path)。path 为 ':memory:' 表示降级为内存监控。"""
    for label, path in _candidates():
        if _probe_write(path):
            conn = _connect(path)
            conn.execute(SCHEMA)
            conn.commit()
            if label not in ("env MONITOR_DB", "workspace"):
                print(
                    f"[md-kb] monitor db fallback -> {path}"
                    "（原位置不可写：通常因 Agent 沙箱将 MCP 进程设为只读，监控自动回退）",
                    file=sys.stderr,
                )
            return conn, path
    print("[md-kb] WARNING: 无可写监控库位置，降级为内存监控（数据不持久化）", file=sys.stderr)
    conn = sqlite3.connect(":memory:")
    conn.execute(SCHEMA)
    conn.commit()
    return conn, ":memory:"


def resolve_for_read():
    """报表/看板用：优先返回已有数据的候选路径，其次存在且可读的，最后默认第一候选。"""
    for _, path in _candidates():
        if _row_count(path) > 0:
            return path
    for _, path in _candidates():
        if _row_count(path) >= 0:
            return path
    return _candidates()[0][1]
