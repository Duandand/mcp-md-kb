# Demo 任务脚本：周报素材整理（端到端）

## 录制前准备（一次性）

1. 把 [trae_mcp_config.json](trae_mcp_config.json) 的内容粘贴到 Trae 的 MCP 配置（`.trae/mcp.json`，或 设置 → MCP → 手动添加），开启 `md-kb` 服务器，确认工具列表出现 4 个工具。配置使用相对路径（假设从项目目录启动）；若 Trae 拉起失败提示找不到解释器，将 `command`/`args` 改为绝对路径；
2. 任务指令中需要带上 api_key（Agent 调用工具时必须传）：本机 `.env` 中为 `demo-key-12345`；
3. Trae 沙箱以只读方式拉起 MCP 进程时，监控库会自动回退到 `~/.md_kb_mcp/monitor.db`（stderr 有提示，属预期行为）；`monitor_query.py` / `monitor_dashboard.py` 会自动定位实际落库路径，无需手动指定。修改过 mcp_server.py 后记得在 Trae MCP 面板重启 `md-kb`。

## Agent 任务指令（可直接粘贴给支持 MCP 的 Agent）

> 请帮我查阅本地 Markdown 知识库，找出与「项目风险」相关的内容：
> 1. 先看看知识库里有哪些笔记；
> 2. 检索包含「项目风险」的片段；
> 3. 挑选最相关的一篇笔记提取摘要，并单独取出它的「项目风险」章节；
> 4. 基于以上素材，整理成一段可直接放进周报的文字（含本周风险与下周关注点）。
>
> 调用 MCP 工具时 api_key 参数使用：demo-key-12345

## 预期 Agent 调用链路

```
用户指令
   │
   ▼
list_markdown_files(kb_root_path, api_key)          ← 发现 5 篇笔记
   │
   ▼
search_markdown_by_keyword(kb_root_path, keyword="项目风险", api_key)
   │                                                 ← 返回匹配片段 + is_relative 标记
   ▼
extract_md_summary(kb_root_path, file_path=<检索返回的相对路径>, api_key)
   │                                                 ← 拿到 title / summary / word_count
   ▼
get_md_section(kb_root_path, file_path=<同一相对路径>, section_title="项目风险", api_key)
   │
   ▼
Agent 汇总输出周报片段
```

## 推荐观察点（体验时关注）

1. Agent 全程只调用 4 个 MCP 工具，参数由 Agent 自主构造；
2. `file_path` 全程复用工具返回的相对路径（呼应工具描述中的约定）；
3. search 返回多条命中（按文件序排列，不保证最相关在前），Agent 需**自主判断**最相关笔记（本任务应选「凤凰平台周报-第40周.md」）——这是展示 Agent 决策能力的自然节点；
4. 任务结束后运行 `./venv/bin/python monitor_query.py`，可见本次任务的调用计数与耗时已写入 `monitor.db`。

## 预期产出示例（周报片段）

> 本周梳理各项目笔记，识别出三类风险：① 凤凰平台的数据标注资源排队，可能影响 SFT 数据集交付节奏；
> ② 智能客服项目的医疗语料合规审查周期偏长（平均滞留 9 天）；③ 数据平台评测口径未冻结导致指标不可比。
> 下周将优先推动标注资源到位与验收用例评审。
