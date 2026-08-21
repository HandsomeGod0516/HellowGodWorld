"""Web research using Miro

Functions for performing web research through external MCP server.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from agentsociety2.config.config import Config
from agentsociety2.logger import get_logger

logger = get_logger()


async def execute_web_research(
    query: str,
    llm: Optional[str] = None,
    agent: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute web research query through Miro MCP server

    Args:
        query: Search query or task description
        llm: LLM model name (default from config)
        agent: Agent configuration name (default from config)

    Returns:
        Dictionary with research result
    """
    mcp_url = Config.get_web_search_api_url()
    mcp_token = Config.get_web_search_api_token()
    default_llm = Config.get_miroflow_default_llm()
    default_agent = Config.get_miroflow_default_agent()

    if not mcp_url:
        return {
            "success": False,
            "error": "WEB_SEARCH_API_URL not set",
            "content": "WEB_SEARCH_API_URL environment variable not configured",
        }

    if not mcp_token:
        return {
            "success": False,
            "error": "WEB_SEARCH_API_TOKEN not set",
            "content": "WEB_SEARCH_API_TOKEN environment variable not configured",
        }

    query = query.strip()
    if not query:
        return {
            "success": False,
            "error": "query is required",
            "content": "Query cannot be empty",
        }

    llm = (llm or default_llm).strip()
    agent = (agent or default_agent).strip()
    task_id = f"miro_web_research_{uuid.uuid4().hex[:8]}"

    logger.info(f"Miro MCP: url={mcp_url}, task_id={task_id}")

    headers = {"Authorization": f"Bearer {mcp_token}"}

    try:
        async with create_mcp_http_client(headers=headers) as http_client:
            async with streamable_http_client(mcp_url, http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if "run_task" not in [t.name for t in tools.tools]:
                        return {
                            "success": False,
                            "error": "run_task tool not found",
                            "content": "Remote MCP server does not provide run_task tool",
                        }

                    result = await session.call_tool(
                        "run_task",
                        {
                            "task_description": query,
                            "llm": llm,
                            "agent": agent,
                        },
                    )

        if result.isError:
            error_blocks = [
                block.text
                for block in result.content
                if hasattr(block, "text") and block.text
            ]
            error_msg = (
                "\n".join(error_blocks).strip() if error_blocks else "Unknown error"
            )
            logger.error(f"Miro MCP run_task returned error: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "content": f"Miro Web Research execution failed: {error_msg}",
            }

        blocks = [
            block.text
            for block in result.content
            if hasattr(block, "text") and block.text
        ]
        content = "\n\n".join(blocks).strip() if blocks else "Remote MCP returned empty"

        return {
            "success": True,
            "content": f"## Miro Web Research (MCP) Results\n\n{content}",
            "query": query,
            "task_id": task_id,
            "mcp_url": mcp_url,
            "llm": llm,
            "agent": agent,
        }

    except Exception as e:
        logger.error(f"Miro MCP connection or execution failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "content": f"Miro Web Research execution failed: {str(e)}",
        }
