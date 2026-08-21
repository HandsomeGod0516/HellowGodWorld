"""Web research data models

Pydantic models for web research results.
"""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class WebResearchResult(BaseModel):
    """Web research result model

    Represents the result of a web research query.
    """

    model_config = ConfigDict(extra="allow")

    query: str = Field(..., description="Search query")
    content: str = Field(..., description="Research result content")
    task_id: Optional[str] = Field(None, description="Task ID")

    # Optional parameters used
    llm: Optional[str] = Field(None, description="LLM model used")
    agent: Optional[str] = Field(None, description="Agent configuration used")
    mcp_url: Optional[str] = Field(None, description="MCP server URL")
