"""Hypothesis management module

Provides functionality for:
- Creating hypotheses
- Reading hypotheses
- Listing hypotheses
- Deleting hypotheses
"""

from agentsociety2.skills.hypothesis.models import (
    HypothesisModel,
    ExperimentGroupModel,
    HypothesisDataModel,
)
from agentsociety2.skills.hypothesis.manager import (
    find_existing_hypotheses,
    get_next_hypothesis_id,
    validate_hypothesis_schema,
    create_hypothesis_structure,
    generate_hypothesis_markdown,
    generate_experiment_markdown,
    generate_sim_settings,
    add_hypothesis,
    add_hypothesis_with_validation,
    get_hypothesis,
    list_hypotheses,
    delete_hypothesis,
)

__all__ = [
    # Models
    "HypothesisModel",
    "ExperimentGroupModel",
    "HypothesisDataModel",
    # Manager
    "find_existing_hypotheses",
    "get_next_hypothesis_id",
    "validate_hypothesis_schema",
    "create_hypothesis_structure",
    "generate_hypothesis_markdown",
    "generate_experiment_markdown",
    "generate_sim_settings",
    "add_hypothesis",
    "add_hypothesis_with_validation",
    "get_hypothesis",
    "list_hypotheses",
    "delete_hypothesis",
]
