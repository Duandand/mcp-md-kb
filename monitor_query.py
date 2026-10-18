# -*- coding: utf-8 -*-
"""监控报表脚本：读取 monitor.db 输出调用统计并导出 csv。

用法：python monitor_query.py
输出：
  1. 各工具调用次数 / 成功率 / 平均耗时
  2. 失败原因分布
  3. 导出 tool_call_stats.csv / failure_stats.csv
"""
import csv
import sqlite3
import sys

from monitor_store import resolve_for_read

# 自动定位监控库：服务端若因沙箱回退到 home/临时目录，此处会自动跟随
MONITOR_DB = resolve_for_read()
print(f"监控库: {MONITOR_DB}")

conn = sqlite3.connect(MONITOR_DB)
try:
    conn.execute("SELECT 1 FROM mcp_calls LIMIT 1")
except sqlite3.OperationalError:
    print("监控表尚不存在（先运行 mcp_server.py 产生调用记录）")
    sys.exit(1)

print("==== 工具调用统计（次数 / 成功率 / 平均耗时ms） ====")
rows = conn.execute(
    """SELECT tool_name,
              COUNT(*) AS call_count,
              ROUND(100.0 * SUM(success) / COUNT(*), 1) AS success_rate,
              ROUND(AVG(duration_ms), 1) AS avg_ms
       FROM mcp_calls GROUP BY tool_name ORDER BY call_count DESC"""
).fetchall()
print(f"{'tool_name':<32}{'call_count':>12}{'success_rate%':>15}{'avg_ms':>10}")
for r in rows:
    print(f"{r[0]:<32}{r[1]:>12}{r[2]:>15}{r[3]:>10}")

print("\n==== 失败原因分布 ====")
rows = conn.execute(
    """SELECT tool_name, error_code, COUNT(*) AS cnt
       FROM mcp_calls WHERE success = 0 GROUP BY tool_name, error_code ORDER BY cnt DESC"""
).fetchall()
print(f"{'tool_name':<32}{'error_code':<24}{'count':>6}")
for r in rows:
    print(f"{r[0]:<32}{r[1]:<24}{r[2]:>6}")

with open("tool_call_stats.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["tool_name", "call_count", "success_rate_pct", "avg_duration_ms"])
    w.writerows(rows)

with open("failure_stats.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["tool_name", "error_code", "count"])
    w.writerows(conn.execute(
        "SELECT tool_name, error_code, COUNT(*) FROM mcp_calls WHERE success=0 GROUP BY tool_name, error_code ORDER BY 3 DESC"
    ).fetchall())

print("\n已导出：tool_call_stats.csv, failure_stats.csv")
conn.close()
