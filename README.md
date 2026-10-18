# MCP Markdown Knowledge Base Server

面向 AI Agent 的本地 Markdown 知识库轻量查询 MCP Server（stdio 模式）。Agent 通过 MCP 协议检索本地笔记、提取摘要、定位章节，无需上传文件到第三方服务。

## 项目背景（文字说明）

### 1. 选题原因与真实问题

**真实痛点**：日常周报、项目笔记、技术文档全部存本地 Markdown。办公 Agent 无法直接读取本地文件；直接把全文粘贴给 Agent 又有两个问题：① 粘贴繁琐、长文本 token 开销巨大；② 敏感笔记不想上传外部 API。

现有方案缺陷：
- 让 Agent 用浏览器读本地文件：浏览器安全策略限制，无法访问本地磁盘；
- 通用文件 MCP：大多面向云存储、整文件读写，缺少 Markdown 标题/段落/片段的精细化提取，返回内容杂乱。

**目标用户**：使用 AI 办公 Agent、有本地 Markdown 知识库的研发/产品（写周报、复盘、查阅历史项目记录）。
**范围边界**：只读不改；先做文件名/关键词文本检索，不做向量检索（最小落地，后续扩展）。

### 2. 解题思路

1. 封装 MCP Server，提供 4 个粒度清晰工具，职责单一，拒绝万能工具；
2. 本地 stdio 模式接入（可选 Streamable HTTP 远程形态），MCP 进程直接读取本地 md 文件夹；
3. API Key 简单鉴权；埋点记录调用指标（调用次数、耗时、成功/失败、失败原因）写入 sqlite；
4. 端到端：Agent 发起任务 → 调用 MCP 工具检索笔记 → 获取摘要 → 完成周报素材整理；
5. 收集监控数据，完成一轮迭代优化，留存改前改后证据。

### 3. 与已有方案对比：为什么不是 REST API / 浏览器 / 现成文件 MCP？

1. **为什么不是单纯 REST API**：REST 需要 Agent 自己解析 http 请求、构造 body、处理错误码；MCP 是 Agent 原生协议——自动理解工具 schema、参数描述、错误语义，自动处理参数校验与重试，原生支持 stdio 进程通信，无需端口、跨域、部署 web 服务。REST 适合人类/后端服务调用；MCP 专门给 Agent 自动调用，工具描述直接给 Agent 看。
2. **为什么不用浏览器**：浏览器沙盒策略下网页 JS 不能读取本地任意 md 文件；开启本地文件协议安全风险高，Agent 稳定控制浏览器滚动与文件读取的失败率极高。
3. **为什么不用现成通用文件 MCP**：开源文件 MCP 大多是整文件读写，没有针对 Markdown 的标题分割、段落片段提取；Agent 拿到整篇长 md 会 token 爆炸、超出上下文。本项目拆分工具、只返回摘要/片段，控制返回长度以适配 Agent 上下文窗口。

### 4. 最大困难与关键决策

**最大困难**：Agent 容易传错参数（路径不存在、关键词为空、文件名不存在），而早期报错信息面向人类可读，Agent 看不懂，会盲目反复重试调用。

**关键决策**：
1. 拆成 4 个单一职责工具，不合并成万能查询，降低 Agent 参数混淆概率；
2. 错误返回结构化 JSON：`error_code` + `human_message` + `agent_guidance`（专门指导 Agent 修正参数）；
3. 监控埋点只用本地 sqlite，不引入 ELK/Prometheus，最小可运营；
4. 优先 stdio 接入（无端口/防火墙问题，端到端最容易跑通），Streamable HTTP 作为可选形态。

## 架构选型与备选方案

| 选型点 | 选择 | 理由 | 放弃的备选 |
|---|---|---|---|
| MCP 框架 | Python 官方 `mcp` SDK（FastMCP） | 原生协议实现，自动处理 stdio 通信、工具 schema、类型校验 | 手写 MCP JSON-RPC：协议细节易错、代价大 |
| 监控存储 | SQLite 单文件 | 无需启动数据库进程，开箱即用，Demo 友好 | csv 日志（统计查询麻烦）、Prometheus（太重，额外部署） |
| 鉴权 | 静态 API Key | 最小安全机制，防止未授权调用，满足硬性要求 | OAuth2：需授权服务，超出最小落地范围 |
| 检索实现 | 纯 Python 字符串查找 | 零依赖向量库、无 embedding 模型，快速跑通 | Chroma/LanceDB 向量检索：增加部署依赖，超出最小可用版本 |

## 工具列表（4 个单一职责工具）

| 工具名 | 职责 | 关键入参 | 返回 |
|---|---|---|---|
| `list_markdown_files` | 列出知识库全部 md 文件 | `kb_root_path` | 相对路径列表 + count |
| `search_markdown_by_keyword` | 关键词检索，返回匹配片段 | `kb_root_path`, `keyword`, `max_results` | `matches[{file_path, snippet, line_no, is_relative}]` |
| `extract_md_summary` | 单文件摘要 | `kb_root_path`, `file_path`(相对) | `title`, `summary`, `word_count` |
| `get_md_section` | 按标题提取章节 | `kb_root_path`, `file_path`(相对), `section_title` | `section_content` |

所有调用需携带 `api_key`（与 `.env` 中 `MCP_API_KEY` 一致）。所有错误返回结构化 JSON：

```json
{
  "success": false,
  "error_code": "FILE_NOT_FOUND",
  "human_message": "Markdown file not found: xxx.md",
  "agent_guidance": "Call list_markdown_files first, then reuse one of its returned relative paths..."
}
```

`agent_guidance` 专门给 Agent 阅读以指导参数修正，减少盲目重试。

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt   # mcp<2, python-dotenv
cp .env.template .env                        # 填写 MCP_API_KEY / KB_ROOT / MONITOR_DB
./venv/bin/python mcp_server.py              # stdio 模式启动（由 Agent 拉起时无需手动运行）
```

> 脱敏说明：仓库只包含 `.env.template`（key 为占位符），真实 `.env` 仅本地使用且已加入 `.gitignore`，不会入库；监控库中 api_key 落库前已脱敏（仅保留前 4 位）。

## Agent 接入配置（stdio）

```json
{
  "mcpServers": {
    "md-kb": {
      "command": "/abs/path/to/mcp-md-kb/venv/bin/python",
      "args": ["/abs/path/to/mcp-md-kb/mcp_server.py"],
      "env": {
        "MCP_API_KEY": "your_key",
        "KB_ROOT": "./sample_notes"
      }
    }
  }
}
```

## 远程接入（可选，Streamable HTTP）

复用同一套工具、鉴权与埋点，适合不支持本地进程的在线办公 Agent：

```bash
./venv/bin/python mcp_server.py --http        # 默认 127.0.0.1:8000/mcp，可用 MCP_HTTP_HOST/MCP_HTTP_PORT 覆盖
```

```json
{
  "mcpServers": {
    "md-kb": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

## 监控（最小可运营）

每次调用（成功/失败）自动写入 sqlite `monitor.db`：工具名、api_key(脱敏)、时间戳、耗时ms、是否成功、error_code。

> 一句话回答：每轮任务链路必调的 `list_markdown_files` / `search_markdown_by_keyword` 调用最多；最容易失败的是 `extract_md_summary`；失败真实原因是 Agent 传参错乱——把不存在的文件名/路径误塞进 `file_path` 甚至 `api_key` 参数位（FILE_NOT_FOUND 为主）。这些失败调用均收到结构化错误 + `agent_guidance`，Agent 随后自纠并跑通完整链路，失败分布可在 [dashboard.html](dashboard.html)（本地重新生成）与 [iteration_before_after.csv](iteration_before_after.csv) 中查看。

```bash
./venv/bin/python monitor_query.py       # 输出调用统计/失败分布，导出 csv
./venv/bin/python monitor_dashboard.py   # 生成 dashboard.html 监控看板（自包含，可直接截图）
./venv/bin/python test_mcp_client.py     # 20 项端到端验证（函数级 + MCP stdio 协议级）
```

## 迭代闭环证据

```bash
./venv/bin/python simulate_iteration.py   # 可复现实验：同一任务/同样 Agent 犯错模式，改前 vs 改后
```

同一「周报任务」× 3 种 Agent 犯错模式 × 20 轮（共 60 任务/阶段）的模拟结果：

| 指标 | 改前 | 改后 | 变化 |
|---|---|---|---|
| 单任务平均 MCP 调用次数 | 4.33 | 3.33 | -23% |
| 失败调用总数 | 60 | 20 | -67% |
| FILE_NOT_FOUND 次数 | 60 | 20 | -67% |
| 单任务最差调用次数 | 7 | 4 | - |

**观测与根因（改前）**：`extract_md_summary` 失败率 50%（60 次失败 / 120 次调用），失败原因 100% 为 `FILE_NOT_FOUND`——Agent 拿到 search 返回的相对路径后手动拼接前缀或误传绝对路径，且无引导时盲目重试（单任务最差 7 次调用）。根因：工具描述未区分「知识库根目录」与「文件相对路径」，返回结果未标准化路径格式。

改动内容：① 工具描述明确「file_path 必须复用相对路径」；② search 返回增加 `is_relative` 标记；③ 错误增加 `agent_guidance` 引导 Agent 自纠错；④ 路径自动规范化（绝对路径自动转相对、去除 `./` 前缀）+ 路径穿越防护。完整数据见 [iteration_before_after.csv](iteration_before_after.csv)。

## 项目结构

```
mcp-md-kb/
├── mcp_server.py              # MCP 主服务（FastMCP, stdio / --http 双形态, 4 工具 + 鉴权 + 埋点）
├── monitor_query.py           # 监控报表：统计输出 + csv 导出
├── monitor_dashboard.py       # 监控看板生成器 → dashboard.html
├── dashboard.html             # 监控看板（自包含 HTML，浏览器打开即可截图）
├── test_mcp_client.py         # 验证脚本：函数级单测 + MCP stdio 协议级测试（20 项）
├── simulate_iteration.py      # 迭代闭环模拟：生成改前/改后对比数据
├── iteration_before_after.csv # 迭代前后量化对比（模拟产出证据）
├── AI_conversation_log.md     # AI 对话记录时间线 + 5 条最有效提示词
├── demo_task.md               # 端到端 Demo 任务脚本
├── .env.template              # 配置模板（key 为占位符，已脱敏）
├── requirements.txt           # 依赖：mcp<2, python-dotenv
├── monitor.db                 # sqlite 监控库（运行时生成）
└── sample_notes/              # 测试知识库（5 篇 md，含子目录）
```

## FAQ

**MCP 服务报 `attempt to write a readonly database`？**
部分 Agent（如 Trae）以沙箱拉起 MCP 子进程，进程对工作区目录只读（文件权限本身正常）。服务启动时由 [monitor_store.py](monitor_store.py) 按序探测可写位置自动回退：`MONITOR_DB` → `./monitor.db` → `~/.md_kb_mcp/monitor.db` → 系统临时目录，并在 stderr 打印回退提示；报表/看板脚本读端自动定位实际落库路径；全部不可写则降级内存监控（stderr 警告，工具调用不受影响）。

## 约束与边界

- 只读不改：所有工具仅读取 md 文件；
- 纯文本检索：不引入向量库/embedding，最小依赖（`mcp<2` + `python-dotenv`）；
- 鉴权为静态 API Key：Demo 场景够用，后续可升级 OAuth；
- 可选扩展：SSE/Streamable HTTP 入口、调用频控、md 清洗（去除代码块）。

## License

[MIT](LICENSE)
