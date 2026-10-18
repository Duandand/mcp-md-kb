# -*- coding: utf-8 -*-
"""MCP Markdown Knowledge Base Server (stdio mode)

提供 4 个单一职责工具，供 AI Agent 通过 MCP 协议查询本地 Markdown 知识库：
  1. list_markdown_files        列出知识库全部 md 文件
  2. search_markdown_by_keyword 关键词检索，返回匹配片段
  3. extract_md_summary         单文件摘要
  4. get_md_section             按标题提取章节

通用约定：
  - 鉴权：每个工具都必须携带 api_key，与 .env 中 MCP_API_KEY 一致；
  - file_path 一律使用 list_markdown_files / search_markdown_by_keyword
    返回的相对路径（相对 kb_root_path），不要手动拼接绝对路径；
  - 所有错误返回结构化 JSON：success / error_code / human_message / agent_guidance，
    其中 agent_guidance 专门给 Agent 阅读，指导其如何修正参数；
  - 每次调用（成功或失败）都会写入 sqlite 监控表 mcp_calls。
"""
import os
import sys
import time
import sqlite3

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()
MCP_API_KEY = os.getenv("MCP_API_KEY", "")

mcp = FastMCP("md-kb-server")

# ---------- 监控埋点（sqlite） ----------
# 要点：部分 Agent（如 Trae）以沙箱拉起 MCP 进程，对工作区只读，直接写
# monitor.db 会报 `attempt to write a readonly database`。由 monitor_store
# 探测第一个可写位置自动回退（全部失败则降级内存），工具调用不受影响。
from monitor_store import resolve_for_write

_conn, MONITOR_DB_PATH = resolve_for_write()
_monitor_warned = False


def record_call(tool_name: str, api_key: str, start_ts: float, success: bool, error_code: str):
    """每次工具调用后写入监控表。api_key 脱敏后落库。
    监控写失败只经 stderr 警告一次，绝不影响工具调用本身。
    """
    global _monitor_warned
    duration_ms = int((time.time() - start_ts) * 1000)
    masked = (api_key[:4] + "***") if api_key else ""
    try:
        _conn.execute(
            "INSERT INTO mcp_calls (tool_name, api_key, timestamp, duration_ms, success, error_code)"
            " VALUES (?,?,?,?,?,?)",
            (tool_name, masked, time.time(), duration_ms, 1 if success else 0, error_code),
        )
        _conn.commit()
    except sqlite3.OperationalError as e:
        if not _monitor_warned:
            print(f"[md-kb] WARNING: 监控写入失败（{e}），工具调用继续，监控数据可能不完整", file=sys.stderr)
            _monitor_warned = True


# ---------- 通用辅助 ----------
def error_response(error_code: str, human_msg: str, agent_guide: str) -> dict:
    return {
        "success": False,
        "error_code": error_code,
        "human_message": human_msg,
        "agent_guidance": agent_guide,
    }


def check_auth(tool_name: str, api_key: str, start_ts: float):
    """校验 api_key。失败返回结构化错误，成功返回 None。"""
    if not MCP_API_KEY or api_key != MCP_API_KEY:
        record_call(tool_name, api_key, start_ts, False, "INVALID_API_KEY")
        return error_response(
            "INVALID_API_KEY",
            "API key invalid or server MCP_API_KEY not configured",
            "Read MCP_API_KEY from server .env file and pass it as api_key parameter",
        )
    return None


def check_kb_root(tool_name: str, kb_root_path: str, start_ts: float):
    """校验知识库根目录。失败返回结构化错误，成功返回规范化后的绝对路径。"""
    root = os.path.abspath(os.path.expanduser(kb_root_path))
    if not os.path.isdir(root):
        record_call(tool_name, kb_root_path, start_ts, False, "PATH_NOT_EXIST")
        return None, error_response(
            "PATH_NOT_EXIST",
            f"Knowledge base directory not found: {kb_root_path}",
            "kb_root_path must be an existing local folder. "
            "Pass the same value as KB_ROOT in the server .env (e.g. ./sample_notes)",
        )
    return root, None


def normalize_rel_path(file_path: str, kb_root_abs: str) -> str:
    """规范化文件路径：
    1. 统一分隔符并去除首尾空白；
    2. 若 Agent 误传了 kb_root 下的绝对路径，自动转为相对路径；
    3. 去除 ./ 前缀与多余层级。
    """
    p = file_path.strip().replace("\\", "/")
    p = os.path.normpath(p)
    if os.path.isabs(p):
        try:
            rel = os.path.relpath(p, kb_root_abs)
            if not rel.startswith(".."):
                p = rel
        except ValueError:
            pass
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def resolve_file(tool_name: str, kb_root_abs: str, file_path: str, start_ts: float):
    """解析并校验 file_path（相对路径），含路径穿越防护。
    返回 (绝对路径, None) 或 (None, 结构化错误)。
    """
    rel = normalize_rel_path(file_path, kb_root_abs)
    full = os.path.normpath(os.path.join(kb_root_abs, rel))
    if not (full == kb_root_abs or full.startswith(kb_root_abs + os.sep)):
        record_call(tool_name, file_path, start_ts, False, "PATH_ESCAPE")
        return None, error_response(
            "PATH_ESCAPE",
            "file_path escapes the knowledge base root",
            "file_path must stay inside kb_root_path. Reuse the relative path returned by "
            "list_markdown_files, do not use ../ or absolute paths outside the knowledge base",
        )
    if not os.path.isfile(full):
        record_call(tool_name, file_path, start_ts, False, "FILE_NOT_FOUND")
        return None, error_response(
            "FILE_NOT_FOUND",
            f"Markdown file not found: {rel}",
            "Call list_markdown_files first, then reuse one of its returned relative paths "
            "as file_path. Do NOT manually construct or concatenate paths",
        )
    return full, None


# ---------- 工具 1：列出全部 md 文件 ----------
@mcp.tool()
async def list_markdown_files(kb_root_path: str, api_key: str) -> dict:
    """List all markdown (.md) files in the local knowledge base folder.

    Call this FIRST to discover available notes, then pick files for
    search_markdown_by_keyword / extract_md_summary / get_md_section.

    Args:
        kb_root_path: local knowledge base root folder, same value as KB_ROOT in server .env
        api_key: auth key, must equal MCP_API_KEY in server .env

    Returns:
        {"success": true, "files": [relative paths...], "count": N}
        All returned paths are RELATIVE to kb_root_path — reuse them as file_path
        in other tools. Never convert them to absolute paths.
    """
    start = time.time()
    err = check_auth("list_markdown_files", api_key, start)
    if err:
        return err
    root, err = check_kb_root("list_markdown_files", kb_root_path, start)
    if err:
        return err

    files = []
    for cur_root, _, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".md"):
                files.append(os.path.relpath(os.path.join(cur_root, fn), root))
    files.sort()
    record_call("list_markdown_files", api_key, start, True, "")
    return {"success": True, "files": files, "count": len(files)}


# ---------- 工具 2：关键词检索 ----------
@mcp.tool()
async def search_markdown_by_keyword(kb_root_path: str, keyword: str, max_results: int = 5, api_key: str = "") -> dict:
    """Search a keyword across all markdown files and return matched snippets.

    Args:
        kb_root_path: local knowledge base root folder, same value as KB_ROOT in server .env
        keyword: non-empty text to search (case-insensitive)
        max_results: max snippets to return (1-50, default 5)
        api_key: auth key, must equal MCP_API_KEY in server .env

    Returns:
        {"success": true, "matches": [{"file_path", "snippet", "line_no", "is_relative": true}]}
        IMPORTANT: file_path in results is a RELATIVE path (is_relative=true).
        Pass it AS-IS to extract_md_summary / get_md_section. Never build an
        absolute path from it.
    """
    tool = "search_markdown_by_keyword"
    start = time.time()
    err = check_auth(tool, api_key, start)
    if err:
        return err
    root, err = check_kb_root(tool, kb_root_path, start)
    if err:
        return err
    if not keyword.strip():
        record_call(tool, api_key, start, False, "EMPTY_KEYWORD")
        return error_response(
            "EMPTY_KEYWORD",
            "keyword is empty",
            "Provide a non-empty keyword, e.g. '项目风险'. If unsure what to search, "
            "call list_markdown_files first to see available notes",
        )
    max_results = max(1, min(int(max_results), 50))

    kw = keyword.lower()
    matches = []
    for cur_root, _, filenames in os.walk(root):
        for fn in sorted(filenames):
            if not fn.lower().endswith(".md"):
                continue
            full_path = os.path.join(cur_root, fn)
            rel_path = os.path.relpath(full_path, root)
            try:
                with open(full_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for idx, line in enumerate(lines):
                if kw in line.lower():
                    matches.append(
                        {
                            "file_path": rel_path,
                            "snippet": line.strip()[:200],
                            "line_no": idx + 1,
                            "is_relative": True,
                        }
                    )
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break
        if len(matches) >= max_results:
            break

    record_call(tool, api_key, start, True, "")
    return {"success": True, "matches": matches, "count": len(matches)}


# ---------- 工具 3：单文件摘要 ----------
@mcp.tool()
async def extract_md_summary(kb_root_path: str, file_path: str, api_key: str) -> dict:
    """Read ONE markdown file and return its title, short summary and word count.

    Args:
        kb_root_path: local knowledge base root folder, same value as KB_ROOT in server .env
        file_path: RELATIVE path of the md file — MUST be reused from the results of
            list_markdown_files or search_markdown_by_keyword (e.g. 'notes/a.md').
            DO NOT pass an absolute path and DO NOT concatenate paths manually.
        api_key: auth key, must equal MCP_API_KEY in server .env

    Returns:
        {"success": true, "title", "summary", "word_count"}
    """
    tool = "extract_md_summary"
    start = time.time()
    err = check_auth(tool, api_key, start)
    if err:
        return err
    root, err = check_kb_root(tool, kb_root_path, start)
    if err:
        return err
    full, err = resolve_file(tool, root, file_path, start)
    if err:
        return err

    try:
        with open(full, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        record_call(tool, api_key, start, False, "READ_ERROR")
        return error_response(
            "READ_ERROR",
            f"read file failed: {e}",
            "The file exists but cannot be read. Check file permission and encoding",
        )

    title = ""
    body_lines = []
    for line in content.splitlines():
        if not title and line.strip().startswith("# "):
            title = line.strip().lstrip("#").strip()
            continue
        body_lines.append(line)
    body = "\n".join(body_lines).strip()
    summary = " ".join(body.split())[:300]
    word_count = len(content.split())
    record_call(tool, api_key, start, True, "")
    return {"success": True, "title": title, "summary": summary, "word_count": word_count}


# ---------- 工具 4：按标题提取章节 ----------
@mcp.tool()
async def get_md_section(kb_root_path: str, file_path: str, section_title: str, api_key: str) -> dict:
    """Extract ONE section (from a heading to the next heading of any level) by its title.

    Args:
        kb_root_path: local knowledge base root folder, same value as KB_ROOT in server .env
        file_path: RELATIVE path of the md file — reuse from list_markdown_files /
            search_markdown_by_keyword results. DO NOT pass an absolute path.
        section_title: heading text to locate, e.g. '项目风险' (case-insensitive, partial match ok)
        api_key: auth key, must equal MCP_API_KEY in server .env

    Returns:
        {"success": true, "section_title", "section_content"}
    """
    tool = "get_md_section"
    start = time.time()
    err = check_auth(tool, api_key, start)
    if err:
        return err
    root, err = check_kb_root(tool, kb_root_path, start)
    if err:
        return err
    if not section_title.strip():
        record_call(tool, api_key, start, False, "EMPTY_SECTION_TITLE")
        return error_response(
            "EMPTY_SECTION_TITLE",
            "section_title is empty",
            "Provide the heading text you want, e.g. '项目风险'. "
            "Call extract_md_summary first if you don't know the section names",
        )
    full, err = resolve_file(tool, root, file_path, start)
    if err:
        return err

    try:
        with open(full, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        record_call(tool, api_key, start, False, "READ_ERROR")
        return error_response(
            "READ_ERROR",
            f"read file failed: {e}",
            "The file exists but cannot be read. Check file permission and encoding",
        )

    target = section_title.strip().lower()
    section_lines = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if in_section:
                break
            if target in stripped.lstrip("#").strip().lower():
                in_section = True
            continue
        if in_section:
            section_lines.append(line)

    if not in_section:
        record_call(tool, api_key, start, False, "SECTION_NOT_FOUND")
        return error_response(
            "SECTION_NOT_FOUND",
            f"section '{section_title}' not found in {file_path}",
            "Check the exact heading text. Call extract_md_summary to inspect the document "
            "structure, or use search_markdown_by_keyword to locate the content instead",
        )

    record_call(tool, api_key, start, True, "")
    return {
        "success": True,
        "section_title": section_title,
        "section_content": "\n".join(section_lines).strip(),
    }


if __name__ == "__main__":
    if "--http" in sys.argv:
        # 远程入口：Streamable HTTP，复用同一套工具与鉴权/埋点。
        # 端点：http://{host}:{port}/mcp ；示例：python mcp_server.py --http
        mcp.settings.host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.getenv("MCP_HTTP_PORT", "8000"))
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
