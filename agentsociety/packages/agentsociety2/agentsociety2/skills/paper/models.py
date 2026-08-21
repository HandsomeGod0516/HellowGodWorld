"""Paper generation data models

Pydantic models for paper generation metadata and configuration.
"""

from __future__ import annotations

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict


class PaperFigure(BaseModel):
    """Figure metadata for paper generation"""

    id: str = Field(..., description="Figure identifier (e.g., 'fig:example')")
    caption: str = Field(..., description="Figure caption")
    description: str = Field(..., description="Figure description")
    file_path: str = Field(..., description="Path to figure file")


class PaperMetadata(BaseModel):
    """Paper metadata for generation

    Contains all metadata needed for paper generation.
    """

    model_config = ConfigDict(extra="allow")

    title: str = Field(..., description="Paper title")
    idea_hypothesis: str = Field(..., description="Research question or hypothesis")
    method: str = Field(..., description="Methodology and design")
    data: str = Field(..., description="Data or validation setup")
    experiments: str = Field(..., description="Experiment design and results")
    references: List[str] = Field(default_factory=list, description="BibTeX references")
    figures: List[PaperFigure] = Field(default_factory=list, description="Figures")
    tables: List[Dict[str, Any]] = Field(default_factory=list, description="Tables")


class PaperGenerationRequest(BaseModel):
    """Request for paper generation"""

    model_config = ConfigDict(extra="allow")

    # Metadata fields
    title: str
    idea_hypothesis: str
    method: str
    data: str
    experiments: str
    references: List[str] = Field(default_factory=list)
    figures: List[PaperFigure] = Field(default_factory=list)
    tables: List[Dict[str, Any]] = Field(default_factory=list)

    # Generation options
    compile_pdf: bool = Field(default=True, description="Whether to compile PDF")
    figures_source_dir: Optional[str] = Field(
        None, description="Directory containing figures"
    )
    save_output: bool = Field(default=True, description="Whether to save output")
    output_dir: Optional[str] = Field(None, description="Output directory")
    template_path: Optional[str] = Field(None, description="LaTeX template path")
    style_guide: Optional[str] = Field(None, description="Style guide name")
    target_pages: int = Field(default=6, description="Target page count")
    enable_review: bool = Field(default=True, description="Enable review loop")
    max_review_iterations: int = Field(default=3, description="Max review iterations")
    enable_vlm_review: bool = Field(default=True, description="Enable VLM review")


class PaperGenerationResult(BaseModel):
    """Result of paper generation"""

    model_config = ConfigDict(extra="allow")

    success: bool
    status: str  # 'ok', 'partial', 'error'
    content: str
    pdf_path: Optional[str] = None
    output_path: Optional[str] = None
    iteration_final_dir: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
