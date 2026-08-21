"""Literature data models

Pydantic models for literature search results and indexing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator

from agentsociety2.logger import get_logger

logger = get_logger()


class LiteratureEntry(BaseModel):
    """Literature entry data model

    Used to standardize literature JSON entries, ensuring data structure consistency and type safety.
    """

    # Basic information
    title: str = Field(..., description="Literature title")
    """Literature title, required field"""

    journal: Optional[str] = Field(None, description="Journal name")
    """Journal or conference name"""

    doi: Optional[str] = Field(None, description="DOI identifier")
    """Digital Object Identifier"""

    abstract: Optional[str] = Field(None, description="Abstract")
    """Literature abstract"""

    # File information
    file_path: str = Field(..., description="File path (relative to workspace root)")
    """Path to the literature file, relative to workspace root"""

    file_type: Literal["markdown", "pdf", "docx", "txt", "md"] = Field(
        ..., description="File type"
    )
    """File type: markdown, pdf, docx, txt, md"""

    # Source information
    source: Literal["literature_search", "user_upload"] = Field(
        ..., description="Literature source"
    )
    """Literature source: literature_search (from literature search tool) or user_upload"""

    # Search related (only when source is literature_search)
    query: Optional[str] = Field(None, description="Search query")
    """Query used to retrieve this literature (only when source is literature_search)"""

    avg_similarity: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Average similarity score (0-1)"
    )
    """Average similarity score between literature and query, range 0-1"""

    # Time information
    saved_at: str = Field(..., description="Save time (ISO format)")
    """Literature save time, ISO 8601 format string"""

    # Other fields (allow extension)
    extra_fields: Optional[Dict[str, Any]] = Field(
        None, description="Other extension fields"
    )
    """Other extension fields for storing additional metadata"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "title": "Example Research Paper",
                "journal": "Journal of Example Studies",
                "doi": "10.1000/example",
                "abstract": "This is an example abstract...",
                "file_path": "papers/Example_Research_Paper_2024-01-01.md",
                "file_type": "markdown",
                "source": "literature_search",
                "query": "example research",
                "avg_similarity": 0.85,
                "saved_at": "2024-01-01T12:00:00",
            }
        },
    )

    @field_validator("saved_at")
    @classmethod
    def validate_saved_at(cls, v: str) -> str:
        """Validate save time format"""
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid ISO format for saved_at: {v}")
        return v

    @field_validator("doi")
    @classmethod
    def validate_doi(cls, v: Optional[str]) -> Optional[str]:
        """Validate DOI format (basic check)"""
        if v is None:
            return v
        # Basic DOI format check: 10.xxxx/xxxx
        if not v.startswith("10."):
            logger.warning(f"DOI format may be invalid: {v}")
        return v


class LiteratureIndex(BaseModel):
    """Literature index data model

    Used to standardize the entire literature index JSON file structure.
    """

    entries: list[LiteratureEntry] = Field(
        default_factory=list, description="List of literature entries"
    )
    """List of all literature entries"""

    version: str = Field(default="1.0", description="Index file version")
    """Index file format version"""

    created_at: Optional[str] = Field(
        None, description="Index creation time (ISO format)"
    )
    """Index creation time, ISO 8601 format string"""

    updated_at: Optional[str] = Field(
        None, description="Index last update time (ISO format)"
    )
    """Index last update time, ISO 8601 format string"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "version": "1.0",
                "created_at": "2024-01-01T12:00:00",
                "updated_at": "2024-01-01T12:00:00",
                "entries": [
                    {
                        "title": "Example Research Paper",
                        "journal": "Journal of Example Studies",
                        "doi": "10.1000/example",
                        "abstract": "This is an example abstract...",
                        "file_path": "papers/Example_Research_Paper_2024-01-01.md",
                        "file_type": "markdown",
                        "source": "literature_search",
                        "query": "example research",
                        "avg_similarity": 0.85,
                        "saved_at": "2024-01-01T12:00:00",
                    }
                ],
            }
        },
    )
