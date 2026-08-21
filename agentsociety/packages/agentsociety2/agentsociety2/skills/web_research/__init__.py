"""Web research module

Provides functionality for performing web research through external services.
"""

from agentsociety2.skills.web_research.models import WebResearchResult
from agentsociety2.skills.web_research.miro import execute_web_research

__all__ = [
    "WebResearchResult",
    "execute_web_research",
]
