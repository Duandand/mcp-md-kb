# -*- coding: utf-8 -*-
"""端到端验证脚本（两部分）：

A. 函数级单测：直接调用 4 个工具函数，覆盖正常参数与错误参数，
   校验结构化错误（error_code / agent_guidance）；
B. MCP stdio 协议级测试：以真实 MCP 客户端身份拉起 mcp_server.py 子进程，
   完成 initialize → list_tools → 逐个 call_tool，验证协议链路。

用法：./venv/bin/python test_mcp_client.py
"""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("MCP_API_KEY", "")
KB = os.getenv("KB_ROOT", "./sample_notes")

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    tag = "PASS" if cond else "FAIL"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{tag}] {name}" + (f" -> {detail}" if detail and not cond else ""))


async def part_a_function_tests():
    print("=" * 60)
    print("Part A. 函数级单测")
    print("=" * 60)
    import mcp_server as srv

    # list_markdown_files
    r = await srv.list_markdown_files(KB, API_KEY)
    check("list: 正常调用", r.get("success") is True and r.get("count") == 5, str(r))
    check("list: 返回相对路径", all(not os.path.isabs(p) for p in r.get("files", [])), str(r.get("files")))

    r = await srv.list_markdown_files(KB, "wrong-key")
    check("list: 鉴权失败返回 INVALID_API_KEY", r.get("error_code") == "INVALID_API_KEY" and "agent_guidance" in r, str(r))

    r = await srv.list_markdown_files("./no_such_dir", API_KEY)
    check("list: 目录不存在返回 PATH_NOT_EXIST", r.get("error_code") == "PATH_NOT_EXIST" and "agent_guidance" in r, str(r))

    # search_markdown_by_keyword
    r = await srv.search_markdown_by_keyword(KB, "项目风险", 10, API_KEY)
    ok = r.get("success") is True and r.get("count", 0) >= 3 and all(m.get("is_relative") for m in r.get("matches", []))
    check("search: 命中 >=3 条且带 is_relative 标记", ok, str(r))

    r = await srv.search_markdown_by_keyword(KB, "", 5, API_KEY)
    check("search: 空关键词返回 EMPTY_KEYWORD", r.get("error_code") == "EMPTY_KEYWORD", str(r))

    r = await srv.search_markdown_by_keyword("./no_such_dir", "风险", 5, API_KEY)
    check("search: 根目录不存在返回 PATH_NOT_EXIST", r.get("error_code") == "PATH_NOT_EXIST", str(r))

    # extract_md_summary
    r = await srv.extract_md_summary(KB, "凤凰平台周报-第40周.md", API_KEY)
    ok = r.get("success") is True and "凤凰平台" in r.get("title", "") and r.get("word_count", 0) > 0
    check("summary: 正常调用返回 title/word_count", ok, str(r))

    # Agent 典型错误1：误传绝对路径（应被自动规范化挽救）
    abs_path = os.path.abspath(os.path.join(KB, "凤凰平台周报-第40周.md"))
    r = await srv.extract_md_summary(KB, abs_path, API_KEY)
    check("summary: 误传绝对路径可自动纠正为相对路径", r.get("success") is True, str(r))

    # Agent 典型错误2：文件不存在
    r = await srv.extract_md_summary(KB, "不存在.md", API_KEY)
    check("summary: 文件不存在返回 FILE_NOT_FOUND + agent_guidance",
          r.get("error_code") == "FILE_NOT_FOUND" and "list_markdown_files" in r.get("agent_guidance", ""), str(r))

    # 路径穿越防护
    r = await srv.extract_md_summary(KB, "../todo.md", API_KEY)
    check("summary: 路径穿越返回 PATH_ESCAPE", r.get("error_code") == "PATH_ESCAPE", str(r))

    # get_md_section
    r = await srv.get_md_section(KB, "凤凰平台周报-第40周.md", "项目风险", API_KEY)
    ok = r.get("success") is True and "标注" in r.get("section_content", "")
    check("section: 提取『项目风险』章节", ok, str(r))

    r = await srv.get_md_section(KB, "凤凰平台周报-第40周.md", "不存在的章节", API_KEY)
    check("section: 章节不存在返回 SECTION_NOT_FOUND", r.get("error_code") == "SECTION_NOT_FOUND", str(r))

    r = await srv.get_md_section(KB, "凤凰平台周报-第40周.md", "", API_KEY)
    check("section: 空标题返回 EMPTY_SECTION_TITLE", r.get("error_code") == "EMPTY_SECTION_TITLE", str(r))


async def part_b_mcp_protocol_test():
    print("=" * 60)
    print("Part B. MCP stdio 协议级测试")
    print("=" * 60)
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=os.path.join(os.getcwd(), "venv", "bin", "python"),
        args=["mcp_server.py"],
        env={**os.environ},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            check("协议: initialize + list_tools 成功",
                  names == ["extract_md_summary", "get_md_section", "list_markdown_files", "search_markdown_by_keyword"],
                  str(names))

            def payload(extra=None):
                d = {"kb_root_path": KB, "api_key": API_KEY}
                d.update(extra or {})
                return d

            def parse(content):
                import json
                return json.loads(content.text)

            r = parse((await session.call_tool("list_markdown_files", payload())).content[0])
            check("协议: list_markdown_files 调用成功", r.get("success") is True and r.get("count") == 5, str(r))

            r = parse((await session.call_tool("search_markdown_by_keyword",
                                               payload({"keyword": "项目风险", "max_results": 5}))).content[0])
            check("协议: search 命中片段", r.get("success") is True and r.get("count", 0) >= 3, str(r)[:120])

            r = parse((await session.call_tool("extract_md_summary",
                                               payload({"file_path": "凤凰平台周报-第40周.md"}))).content[0])
            check("协议: extract_md_summary 返回摘要", r.get("success") is True and len(r.get("summary", "")) > 20, str(r)[:120])

            r = parse((await session.call_tool("get_md_section",
                                               payload({"file_path": "凤凰平台周报-第40周.md", "section_title": "项目风险"}))).content[0])
            check("协议: get_md_section 返回章节", r.get("success") is True and "标注" in r.get("section_content", ""), str(r)[:120])

            bad = {"kb_root_path": KB, "api_key": API_KEY, "file_path": "不存在.md"}
            r = parse((await session.call_tool("extract_md_summary", bad)).content[0])
            check("协议: 错误路径返回结构化 agent_guidance",
                  r.get("success") is False and r.get("error_code") == "FILE_NOT_FOUND" and "agent_guidance" in r, str(r))


async def main():
    await part_a_function_tests()
    await part_b_mcp_protocol_test()
    print("=" * 60)
    print(f"结果：PASS={PASS} FAIL={FAIL}")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
