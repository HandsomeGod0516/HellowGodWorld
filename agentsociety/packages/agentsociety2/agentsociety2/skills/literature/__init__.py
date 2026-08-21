"""Literature search and management module

Provides functionality for:
- Searching academic literature
- Saving and indexing literature in workspace
- Formatting literature entries
"""

from agentsociety2.skills.literature.models import LiteratureEntry, LiteratureIndex
from agentsociety2.skills.literature.formatter import (
    sanitize_filename,
    format_article_as_markdown,
)
from agentsociety2.skills.literature.search import (
    search_literature_and_save,
    generate_summary,
    format_search_results,
    load_literature_index,
)
from agentsociety2.skills.literature.core import (
    search_literature,
    is_chinese_text,
)

__all__ = [
    # Models
    "LiteratureEntry",
    "LiteratureIndex",
    # Formatter
    "sanitize_filename",
    "format_article_as_markdown",
    # Search
    "search_literature_and_save",
    "generate_summary",
    "format_search_results",
    "load_literature_index",
    # Core
    "search_literature",
    "is_chinese_text",
]
