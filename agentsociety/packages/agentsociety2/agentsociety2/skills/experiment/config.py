"""Experiment configuration utilities

Functions for reading and validating experiment configurations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Optional

from agentsociety2.logger import get_logger

logger = get_logger()


def read_hypothesis_info(hyp_dir: Path) -> Optional[str]:
    """Read hypothesis information from HYPOTHESIS.md

    Args:
        hyp_dir: Path to hypothesis directory

    Returns:
        Hypothesis markdown content or None if file doesn't exist
    """
    hyp_md = hyp_dir / "HYPOTHESIS.md"
    if hyp_md.exists():
        return hyp_md.read_text(encoding="utf-8")
    return None


def read_experiment_info(exp_dir: Path) -> Optional[str]:
    """Read experiment information from EXPERIMENT.md

    Args:
        exp_dir: Path to experiment directory

    Returns:
        Experiment markdown content or None if file doesn't exist
    """
    exp_md = exp_dir / "EXPERIMENT.md"
    if exp_md.exists():
        return exp_md.read_text(encoding="utf-8")
    return None


def read_sim_settings(hyp_dir: Path) -> Dict[str, Any]:
    """Read SIM_SETTINGS.json

    Args:
        hyp_dir: Path to hypothesis directory

    Returns:
        SIM_SETTINGS dictionary or empty dict if file doesn't exist
    """
    sim_settings_file = hyp_dir / "SIM_SETTINGS.json"
    if sim_settings_file.exists():
        try:
            return json.loads(sim_settings_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse SIM_SETTINGS.json: {e}")
    return {}


def read_init_config(exp_dir: Path) -> Optional[Dict[str, Any]]:
    """Read init_config.json from experiment init directory

    Args:
        exp_dir: Path to experiment directory

    Returns:
        Init config dictionary or None if file doesn't exist
    """
    init_config_file = exp_dir / "init" / "init_config.json"
    if init_config_file.exists():
        try:
            return json.loads(init_config_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to parse init_config.json: {e}")
    return None


def get_experiment_paths(
    workspace_path: Path,
    hypothesis_id: str,
    experiment_id: str,
) -> Dict[str, Path]:
    """Get all relevant paths for an experiment

    Args:
        workspace_path: Path to workspace directory
        hypothesis_id: Hypothesis ID (e.g., '1', '2')
        experiment_id: Experiment ID (e.g., '1', '2')

    Returns:
        Dictionary with all relevant paths

    Note:
        Simplified structure: init/ directory contains config files directly
        - init/config_params.py - Parameter generation script
        - init/init_config.json - Experiment configuration
        - init/steps.yaml - Execution steps
    """
    hyp_dir = workspace_path / f"hypothesis_{hypothesis_id}"
    exp_dir = hyp_dir / f"experiment_{experiment_id}"
    init_dir = exp_dir / "init"
    run_dir = exp_dir / "run"

    return {
        "workspace": workspace_path,
        "hypothesis": hyp_dir,
        "experiment": exp_dir,
        "init": init_dir,
        "run": run_dir,
        "hypothesis_md": hyp_dir / "HYPOTHESIS.md",
        "experiment_md": exp_dir / "EXPERIMENT.md",
        "sim_settings": hyp_dir / "SIM_SETTINGS.json",
        "init_config": init_dir / "init_config.json",
        "steps": init_dir / "steps.yaml",
        "config_params": init_dir / "config_params.py",
        "pid_file": run_dir / "pid.json",
        "stdout_log": run_dir / "stdout.log",
        "stderr_log": run_dir / "stderr.log",
    }


def validate_experiment_exists(
    workspace_path: Path,
    hypothesis_id: str,
    experiment_id: str,
) -> Dict[str, Any]:
    """Validate that an experiment directory structure exists

    Args:
        workspace_path: Path to workspace directory
        hypothesis_id: Hypothesis ID (e.g., '1', '2')
        experiment_id: Experiment ID (e.g., '1', '2')

    Returns:
        Dictionary with validation result and error message if any
    """
    paths = get_experiment_paths(workspace_path, hypothesis_id, experiment_id)

    if not paths["hypothesis"].exists():
        return {
            "valid": False,
            "error": "Hypothesis not found",
            "content": f"Hypothesis directory not found: {paths['hypothesis']}",
        }

    if not paths["experiment"].exists():
        return {
            "valid": False,
            "error": "Experiment not found",
            "content": f"Experiment directory not found: {paths['experiment']}",
        }

    return {
        "valid": True,
        "paths": {k: str(v) for k, v in paths.items()},
    }
