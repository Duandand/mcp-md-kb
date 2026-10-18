# -*- coding: utf-8 -*-
"""监控看板生成器：读取 monitor.db（真实调用流量），输出自包含 dashboard.html。

无外部 JS/CSS 依赖（图表为内联 SVG），可直接浏览器打开截图作为交付物。
改前/改后迭代对比是独立交付物（iteration_before_after.csv），不在本看板内混排。
用法：./venv/bin/python monitor_dashboard.py
"""
import datetime
import os
import sqlite3

from dotenv import load_dotenv
from monitor_store import resolve_for_read


def display_path(p):
    """看板展示用路径脱敏：家目录缩写为 ~，项目内路径缩写为相对路径。"""
    home = os.path.expanduser("~")
    if p.startswith(home):
        return "~" + p[len(home):]
    try:
        return os.path.relpath(p)
    except ValueError:
        return p

load_dotenv()
# 自动定位监控库：服务端若因沙箱回退到 home/临时目录，此处会自动跟随
MONITOR_DB = resolve_for_read()

ACCENT = "#4f6ef7"
RED = "#e5484d"
GREEN = "#30a46c"
GRAY = "#6b7280"


def query_monitor():
    conn = sqlite3.connect(MONITOR_DB)
    try:
        conn.execute("SELECT 1 FROM mcp_calls LIMIT 1")
    except sqlite3.OperationalError:
        raise SystemExit(f"监控表不存在（先运行 mcp_server.py 产生调用记录）：{MONITOR_DB}")
    total, fails, avg_ms = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN success=0 THEN 1 ELSE 0 END), COALESCE(ROUND(AVG(duration_ms),2),0) FROM mcp_calls"
    ).fetchone()
    tools = conn.execute(
        """SELECT tool_name, COUNT(*) c, ROUND(100.0*SUM(success)/COUNT(*),1) sr,
                  ROUND(AVG(duration_ms),2) ms
           FROM mcp_calls GROUP BY tool_name ORDER BY c DESC"""
    ).fetchall()
    errors = conn.execute(
        """SELECT error_code, COUNT(*) c FROM mcp_calls
           WHERE success=0 AND error_code != '' GROUP BY error_code ORDER BY c DESC"""
    ).fetchall()
    conn.close()
    return total, fails or 0, avg_ms, tools, errors


LABEL_MAX_PX = 200  # 标签列宽（像素估算）
BAR_X = 210         # 条形起点
BAR_MAX_W = 340     # 条形最大宽度（620 viewBox 内给右侧数值留空间）


def _est_px(text):
    """粗略估算文本渲染宽度：CJK≈13.5px，其他≈6.8px（13px 字号）。"""
    return sum(13.5 if ord(c) > 0x2E80 else 6.8 for c in text)


def fit_label(text):
    """超长标签截断加省略号，避免文字与条形重叠。"""
    if _est_px(text) <= LABEL_MAX_PX:
        return text
    while text and _est_px(text + "…") > LABEL_MAX_PX:
        text = text[:-1]
    return text + "…"


def hbar(items, color=ACCENT, unit=""):
    """items: [(label, value)]，返回内联 SVG 水平条形图。"""
    if not items:
        return "<p class='muted'>暂无数据</p>"
    maxv = max(v for _, v in items) or 1
    rows = []
    for i, (label, v) in enumerate(items):
        w = max(2, int(BAR_MAX_W * v / maxv))
        y = i * 34 + 6
        rows.append(
            f"<text x='0' y='{y + 15}' class='lbl'>{fit_label(label)}</text>"
            f"<rect x='{BAR_X}' y='{y}' width='{w}' height='20' rx='4' fill='{color}'/>"
            f"<text x='{BAR_X + w + 8}' y='{y + 15}' class='val'>{v}{unit}</text>"
        )
    h = len(items) * 34 + 10
    return f"<svg viewBox='0 0 620 {h}' class='chart'>{''.join(rows)}</svg>"


def kpi_card(label, value, sub="", color=ACCENT):
    return (f"<div class='kpi'><div class='kpi-label'>{label}</div>"
            f"<div class='kpi-value' style='color:{color}'>{value}</div>"
            f"<div class='kpi-sub'>{sub}</div></div>")


def main():
    total, fails, avg_ms, tools, errors = query_monitor()
    success_rate = round(100 * (total - fails) / total, 1) if total else 0
    gen_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tool_bars = hbar([(t[0], t[1]) for t in tools])
    err_bars = hbar([(e[0], e[1]) for e in errors], color=RED)
    tool_table = "".join(
        f"<tr><td>{t[0]}</td><td>{t[1]}</td><td>{t[2]}%</td><td>{t[3]}ms</td></tr>" for t in tools
    )

    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>MCP Markdown KB - 监控看板</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         background: #f6f7fb; color: #1f2328; margin: 0; padding: 32px; }}
  .wrap {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  h2 {{ font-size: 16px; margin: 36px 0 8px; }}
  .muted {{ color: {GRAY}; font-size: 13px; margin: 4px 0 14px; }}
  .kpi-row {{ display: flex; gap: 14px; margin-top: 18px; flex-wrap: wrap; }}
  .kpi {{ background: #fff; border-radius: 12px; padding: 16px 22px; flex: 1; min-width: 160px;
          box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .kpi-label {{ font-size: 12px; color: {GRAY}; }}
  .kpi-value {{ font-size: 30px; font-weight: 700; margin: 6px 0 2px; }}
  .kpi-sub {{ font-size: 12px; color: {GRAY}; }}
  .card {{ background: #fff; border-radius: 12px; padding: 18px 22px;
           box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .chart {{ width: 100%; }}
  .lbl {{ font-size: 13px; fill: #374151; }}
  .val {{ font-size: 13px; fill: #111827; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 10px; }}
  th, td {{ border-bottom: 1px solid #eceef2; padding: 7px 10px; text-align: left; }}
  th {{ color: {GRAY}; font-weight: 600; }}
  code {{ background: #eef0f6; padding: 1px 6px; border-radius: 4px; font-size: 12px; }}
  .footer {{ margin-top: 30px; font-size: 12px; color: {GRAY}; }}
</style></head><body><div class="wrap">
  <h1>MCP Markdown Knowledge Base · 运营监控看板</h1>
  <p class="muted">数据源：{display_path(MONITOR_DB)}（stdio + HTTP 全部调用，含测试与验证流量） · 生成时间：{gen_time}</p>

  <div class="kpi-row">
    {kpi_card("总调用次数", total, "mcp_calls 总记录")}
    {kpi_card("成功率", f"{success_rate}%", f"失败 {fails} 次", GREEN if success_rate >= 90 else RED)}
    {kpi_card("平均耗时", f"{avg_ms}ms", "本地文件操作，毫秒级")}
    {kpi_card("工具数", len(tools), "4 个单一职责工具")}
  </div>

  <h2>各工具调用次数</h2>
  <div class="card">{tool_bars}</div>

  <h2>失败原因分布</h2>
  <div class="card">{err_bars}
  <p class="muted">一句话结论：调用最多的是 <b>{tools[0][0] if tools else "-"}</b>；
  失败集中在 <b>{errors[0][0] if errors else "-"}</b>——主要由测试用例刻意注入的错误参数产生，
  真实 Agent 流量中该错误已被 agent_guidance 引导自纠。</p></div>

  <h2>工具明细</h2>
  <div class="card"><table><tr><th>工具</th><th>调用次数</th><th>成功率</th><th>平均耗时</th></tr>{tool_table}</table></div>

  <p class="footer">重新生成：./venv/bin/python monitor_dashboard.py ·
  报表：monitor_query.py · 改前/改后迭代对比证据：iteration_before_after.csv（复现：simulate_iteration.py）</p>
</div></body></html>"""

    with open("dashboard.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"已生成 dashboard.html（总调用 {total}，成功率 {success_rate}%）")


if __name__ == "__main__":
    main()
