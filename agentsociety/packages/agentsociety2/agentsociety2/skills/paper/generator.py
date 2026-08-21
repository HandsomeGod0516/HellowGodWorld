"""Paper generation functionality

Functions for generating academic papers from experiment results.
"""

from __future__ import annotations

import json
import httpx
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentsociety2.config.config import Config
from agentsociety2.logger import get_logger

logger = get_logger()

# Image formats supported for paper generation
SUPPORTED_IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


def get_easypaper_url() -> str:
    """Get EasyPaper API URL from config"""
    return Config.get_easypaper_api_url()


async def generate_paper_from_metadata(
    metadata: Dict[str, Any],
    output_dir: Path,
    figures_source_dir: Optional[Path] = None,
    template_path: Optional[str] = None,
    style_guide: Optional[str] = None,
    target_pages: int = 6,
    enable_review: bool = True,
    max_review_iterations: int = 3,
    enable_vlm_review: bool = True,
) -> Dict[str, Any]:
    """Generate paper from metadata using EasyPaper API

    Args:
        metadata: Paper metadata dictionary
        output_dir: Output directory for generated files
        figures_source_dir: Directory containing figures
        template_path: Optional LaTeX template .zip path
        style_guide: Style guide name (e.g., 'COLM', 'ICML')
        target_pages: Target page count
        enable_review: Enable EasyPaper's review loop
        max_review_iterations: Maximum review iterations
        enable_vlm_review: Enable VLM-based layout review

    Returns:
        Dictionary with generation result
    """
    api_url = get_easypaper_url()

    # Prepare figures source directory
    if figures_source_dir is None or not figures_source_dir.is_dir():
        figures_source_dir = output_dir / "assets"
        if not figures_source_dir.is_dir():
            figures_source_dir = output_dir

    # Build request body
    request_body: Dict[str, Any] = {
        **metadata,
        "compile_pdf": True,
        "figures_source_dir": str(figures_source_dir.resolve()),
        "save_output": True,
        "enable_review": bool(enable_review),
        "max_review_iterations": max(1, int(max_review_iterations))
        if enable_review
        else 1,
        "enable_vlm_review": bool(enable_vlm_review),
        "target_pages": int(target_pages),
        "output_dir": str(output_dir.resolve()),
    }

    # Handle template
    if template_path:
        if Path(template_path).is_file():
            request_body["template_path"] = template_path
        else:
            logger.warning(f"template_path not found: {template_path}")
            style_guide = "plain"

    if not template_path and not style_guide:
        style_guide = "plain"
    elif style_guide:
        request_body["style_guide"] = style_guide

    # Save metadata.json for reproducibility
    metadata_json_path = output_dir / "metadata.json"
    try:
        metadata_json_path.write_text(
            json.dumps(request_body, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Saved metadata.json to {metadata_json_path}")
    except Exception as e:
        logger.warning(f"Failed to write metadata.json: {e}")

    # Call EasyPaper API
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                f"{api_url.rstrip('/')}/metadata/generate",
                json=request_body,
            )

        if response.status_code != 200:
            return {
                "success": False,
                "error": f"API {response.status_code}",
                "content": f"EasyPaper API error: {response.status_code} {response.text}",
            }

        result = response.json()
        ep_status = result.get("status", "ok")
        ep_errors = result.get("errors", [])
        output_path = result.get("output_path", "")
        pdf_path = result.get("pdf_path", "")

        if ep_status == "error":
            err_msg = "; ".join(ep_errors[:5]) if ep_errors else "Unknown error"
            return {
                "success": False,
                "error": "EasyPaper status=error",
                "content": (
                    f"EasyPaper generation failed (status=error). Errors: {err_msg}"
                ),
                "easypaper_status": ep_status,
                "easypaper_errors": ep_errors,
                "output_path": output_path,
                "metadata_json_path": str(metadata_json_path),
            }

        # Find iteration_final directory
        iteration_final_dirs = sorted(
            output_dir.glob("iteration_*_final"),
            key=lambda p: p.name,
            reverse=True,
        )
        iteration_final_dir = (
            str(iteration_final_dirs[0]) if iteration_final_dirs else None
        )

        content_msg = (
            f"Paper generation completed (status={ep_status}). "
            f"Output directory: {output_path or output_dir}. "
            f"PDF: {pdf_path or 'not generated'}."
        )
        if iteration_final_dir:
            content_msg += f" Complete files in {Path(iteration_final_dir).name}/."
        if ep_status == "partial" and ep_errors:
            content_msg += f" Warnings: {'; '.join(ep_errors[:3])}."

        return {
            "success": True,
            "content": content_msg,
            "pdf_path": pdf_path,
            "output_path": output_path or str(output_dir),
            "iteration_final_dir": iteration_final_dir,
            "easypaper_status": ep_status,
            "metadata": metadata,
            "metadata_json_path": str(metadata_json_path),
            "easypaper_response": result,
        }

    except httpx.ConnectError as e:
        logger.error(f"EasyPaper connection failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "content": f"Cannot connect to EasyPaper. Ensure it is running (EASYPAPER_API_URL={api_url}).",
        }
    except Exception as e:
        logger.error(f"Generate paper failed: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "content": str(e),
        }


def collect_figure_paths_under(base_dir: Path) -> List[str]:
    """Recursively collect image file paths under base_dir

    Args:
        base_dir: Base directory to search

    Returns:
        List of relative paths to image files
    """
    out: List[str] = []
    if not base_dir.is_dir():
        return out
    for p in sorted(base_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in SUPPORTED_IMAGE_FORMATS:
            continue
        try:
            rel = p.relative_to(base_dir)
            out.append(str(rel.as_posix()))
        except ValueError:
            continue
    return out


def read_text_safe(path: Path) -> str:
    """Read file content; return empty string if missing or error"""
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def gather_synthesis_context(
    workspace_path: Path,
    hypothesis_id: str,
    experiment_id: str,
) -> Dict[str, Any]:
    """Gather context for paper synthesis from workspace

    Args:
        workspace_path: Path to workspace
        hypothesis_id: Hypothesis ID
        experiment_id: Experiment ID

    Returns:
        Dictionary with synthesis context
    """
    out: Dict[str, Any] = {
        "topic": "",
        "hypothesis": "",
        "experiment": "",
        "analysis_report": "",
        "analysis_result_json": "",
        "figure_names": [],
        "literature_titles": [],
    }

    # Workspace-level: TOPIC.md
    topic_file = workspace_path / "TOPIC.md"
    out["topic"] = read_text_safe(topic_file)

    # Hypothesis-level: HYPOTHESIS.md
    hyp_file = workspace_path / f"hypothesis_{hypothesis_id}" / "HYPOTHESIS.md"
    out["hypothesis"] = read_text_safe(hyp_file)

    # Experiment-level: EXPERIMENT.md
    exp_dir = (
        workspace_path / f"hypothesis_{hypothesis_id}" / f"experiment_{experiment_id}"
    )
    exp_file = exp_dir / "EXPERIMENT.md"
    out["experiment"] = read_text_safe(exp_file)

    # Analysis outputs: try presentation/ first, then hypothesis_X/experiment_Y/
    pres_dir = (
        workspace_path
        / "presentation"
        / f"hypothesis_{hypothesis_id}"
        / f"experiment_{experiment_id}"
    )
    report_md_pres = pres_dir / "report.md"
    report_md_exp = exp_dir / "report.md"
    out["analysis_report"] = read_text_safe(report_md_pres) or read_text_safe(
        report_md_exp
    )

    result_pres = pres_dir / "data" / "analysis_summary.json"
    result_exp = exp_dir / "data" / "analysis_summary.json"
    out["analysis_result_json"] = read_text_safe(result_pres) or read_text_safe(
        result_exp
    )

    # Collect figure paths
    figure_paths_set: set[str] = set()
    for assets_dir in (pres_dir / "assets", exp_dir / "assets"):
        for rel in collect_figure_paths_under(assets_dir):
            figure_paths_set.add(rel)
    out["figure_names"] = sorted(figure_paths_set)

    # Optional literature
    lit_file = workspace_path / "papers" / "literature_index.json"
    if lit_file.exists():
        try:
            data = json.loads(lit_file.read_text(encoding="utf-8"))
            entries = data.get("entries", [])
            out["literature_titles"] = [
                e.get("title", "") for e in entries if e.get("title")
            ][:15]
        except Exception:
            pass

    return out
