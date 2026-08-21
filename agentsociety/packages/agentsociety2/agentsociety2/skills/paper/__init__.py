"""Paper generation module

Provides functionality for generating academic papers from experiment results.
"""

from agentsociety2.skills.paper.models import (
    PaperFigure,
    PaperMetadata,
    PaperGenerationRequest,
    PaperGenerationResult,
)
from agentsociety2.skills.paper.generator import (
    get_easypaper_url,
    generate_paper_from_metadata,
    collect_figure_paths_under,
    read_text_safe,
    gather_synthesis_context,
    SUPPORTED_IMAGE_FORMATS,
)

__all__ = [
    # Models
    "PaperFigure",
    "PaperMetadata",
    "PaperGenerationRequest",
    "PaperGenerationResult",
    # Generator
    "get_easypaper_url",
    "generate_paper_from_metadata",
    "collect_figure_paths_under",
    "read_text_safe",
    "gather_synthesis_context",
    "SUPPORTED_IMAGE_FORMATS",
]
