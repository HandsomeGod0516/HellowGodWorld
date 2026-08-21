#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Implicit Association Test (IAT) Experiment - Main Entry Point
Run IAT experiment using agentsociety2 framework with profile txt files
"""
import asyncio
import csv
import json
import logging
import os
import re
import glob
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from agentsociety2.contrib.env.implicit_association_test import ImplicitAssociationTestEnv
from agentsociety2.agent import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.society import AgentSociety
from agentsociety2.logger import setup_logging, get_logger


def extract_profile_summary(profile_path: str) -> str:
    """
    Extract Profile Summary section from txt file (lines 45-47, the "## Profile Summary" section).
    
    Args:
        profile_path: Path to the profile txt file
    
    Returns:
        Profile Summary text (lines starting from "## Profile Summary")
    """
    try:
        with open(profile_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        # Find the line index of "## Profile Summary"
        summary_start_idx = None
        for i, line in enumerate(lines):
            if line.strip() == "## Profile Summary":
                summary_start_idx = i
                break
        
        if summary_start_idx is None:
            raise ValueError(f"Profile Summary section not found in {profile_path}")
        
        # Extract from "## Profile Summary" to the end (lines 45-47, 0-indexed: 44-46)
        summary_lines = lines[summary_start_idx:]
        profile_summary = "".join(summary_lines).strip()
        
        return profile_summary
    except Exception as e:
        raise Exception(f"Failed to extract profile summary from {profile_path}: {e}")


def get_participant_id_from_filename(filename: str) -> int:
    """
    Extract participant ID from filename (e.g., P101_profile.txt -> 101)
    
    Args:
        filename: Filename
    
    Returns:
        Participant ID (integer)
    """
    match = re.search(r'P(\d+)_profile\.txt', filename)
    if match:
        return int(match.group(1))
    raise ValueError(f"Cannot extract participant ID from filename: {filename}")


def create_default_profile(agent_id: int) -> str:
    """Create default profile summary for an agent when profile file is not available"""
    return f"""## Profile Summary

This participant is a typical individual with average personality traits and characteristics. 
They demonstrate moderate levels of extraversion, agreeableness, conscientiousness, neuroticism, and openness. 
Their self-concept is balanced, showing neither extreme self-enhancement nor excessive modesty. 
They approach decisions with reasonable consideration of their own interests and values."""


def load_profiles_from_directory(
    profiles_dir: str,
    participant_ids: list[int] = None,
    start_idx: int = 0,
    num_agents: int = None,
) -> list[dict]:
    """
    Load participant profiles from directory, extracting only Profile Summary section.
    
    Args:
        profiles_dir: Profile files directory path
        participant_ids: Specified participant ID list (optional)
        start_idx: Start index (if participant_ids not specified)
        num_agents: Number of agents to load (if participant_ids not specified)
    
    Returns:
        List of dictionaries containing profile information:
        - id: Participant ID
        - profile_text: Profile Summary text (lines 45-47)
        - filename: Filename
    """
    # Get all profile files
    profile_files = sorted(glob.glob(os.path.join(profiles_dir, "P*_profile.txt")))
    
    if not profile_files:
        raise ValueError(f"No profile files found in directory: {profiles_dir}")
    
    profiles = []
    for filepath in profile_files:
        filename = os.path.basename(filepath)
        participant_id = get_participant_id_from_filename(filename)
        profile_summary = extract_profile_summary(filepath)
        
        profiles.append({
            "id": participant_id,
            "profile_text": profile_summary,
            "filename": filename,
        })
    
    # Sort by ID
    profiles.sort(key=lambda x: x["id"])
    
    # Filter by participant_ids if specified
    if participant_ids:
        profile_dict = {p["id"]: p for p in profiles}
        selected_profiles = []
        for pid in participant_ids:
            if pid in profile_dict:
                selected_profiles.append(profile_dict[pid])
            else:
                logging.warning(f"Participant ID {pid} not found in profiles")
        profiles = selected_profiles
    
    # Slice if start_idx and num_agents specified
    elif num_agents is not None:
        profiles = profiles[start_idx : start_idx + num_agents]
    
    return profiles


async def main(
    logger,
    num_agents: int = 5,
    profile_start_idx: int = 0,
    participant_ids: list[int] = None,
    profiles_dir: str = None,
    num_steps: int = 50,
    tick_seconds: int = 60,
):
    """
    Run Implicit Association Test (IAT) experiment using txt profile files.
    
    Experiment setup:
    - Agents complete IAT trials (132 trials total)
    - Each agent must complete all trials in order
    - Results are saved to JSON and CSV files
    
    Args:
        logger: Logger instance
        num_agents: Number of agents (if participant_ids not specified)
        profile_start_idx: Start index (if participant_ids not specified)
        participant_ids: Specified participant ID list (optional, e.g., [101, 102, 103])
        profiles_dir: Profile files directory path (optional, default uses relative path)
        num_steps: Number of simulation steps (default: 50)
        tick_seconds: Seconds per step (default: 60)
    """
    logger.info("\n" + "=" * 80)
    logger.info("Implicit Association Test (IAT) Experiment")
    logger.info("=" * 80)
    logger.info("Experiment Configuration:")
    logger.info(f"  - Number of steps: {num_steps}")
    logger.info(f"  - Seconds per step: {tick_seconds}")
    logger.info(f"  - Agent count: {num_agents if participant_ids is None else len(participant_ids)}")
    logger.info("=" * 80)

    # Experiment parameters
    START_TIME = datetime.now()
    TIME_STEP_SECONDS = tick_seconds
    TOTAL_STEPS = num_steps

    # Storage for cleanup
    iat_env = None
    env_router = None
    agents = []

    # ==================== Load Profiles ====================
    logger.info("\n[Step 1/4] Loading profile txt files (Profile Summary section only)...")
    
    # Determine profile directory path
    if profiles_dir is None:
        # Default path: relative to current file, pointing to Self_bias_dataset directory
        current_file_dir = os.path.dirname(os.path.abspath(__file__))
        workspace_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file_dir)))
        profiles_dir = os.path.join(
            workspace_root,
            "Self_bias_dataset",
            "0.Self_reported_scales",
            "Participant_profiles"
        )
    
    # Load profiles
    profiles = []
    use_default_profiles = False
    
    if not os.path.exists(profiles_dir):
        logger.warning(f"  [WARNING] Profile directory does not exist: {profiles_dir}")
        logger.info(f"  [INFO] Will use default agent profiles")
        use_default_profiles = True
    else:
        logger.info(f"  [OK] Profile directory: {profiles_dir}")
        
        try:
            profiles = load_profiles_from_directory(
                profiles_dir=profiles_dir,
                participant_ids=participant_ids,
                start_idx=profile_start_idx,
                num_agents=num_agents if participant_ids is None else None,
            )
        except Exception as e:
            logger.warning(f"  [WARNING] Failed to load profile files: {e}")
            logger.info(f"  [INFO] Will use default agent profiles")
            use_default_profiles = True
    
    # If no profiles loaded, create default profiles
    if use_default_profiles or not profiles:
        logger.info(f"  [INFO] Creating default agent profiles")
        if participant_ids:
            target_agent_count = len(participant_ids)
            agent_ids = participant_ids
        elif num_agents is not None:
            target_agent_count = num_agents
            agent_ids = [1000 + i for i in range(target_agent_count)]
        else:
            target_agent_count = 5  # Default to 5 agents
            agent_ids = [1000 + i for i in range(target_agent_count)]
        
        for agent_id in agent_ids:
            default_profile = create_default_profile(agent_id)
            profiles.append({
                "id": agent_id,
                "profile_text": default_profile,
                "filename": f"default_agent_{agent_id}.txt",
            })
    
    logger.info(f"  [OK] Loaded {len(profiles)} agent profiles")
    
    # Get actual agent IDs
    actual_agent_ids = [p["id"] for p in profiles]
    logger.info(f"  [OK] Actual Agent IDs: {actual_agent_ids}")
    logger.info(f"  [OK] Profile files used: {[p['filename'] for p in profiles]}")
    
    # Show sample profile summary
    if profiles:
        logger.info(f"  [OK] Profile Summary sample (first 200 characters):")
        sample_summary = profiles[0]["profile_text"][:200]
        logger.info(f"    {sample_summary}...")

    # ==================== Initialize Environment ====================
    logger.info("\n[Step 2/4] Initializing environment...")

    # Create temporary directory for chroma memories
    import tempfile
    chroma_base_dir = tempfile.mkdtemp(prefix="chroma_iat_")
    logger.info(f"  [OK] Created temporary chroma directory: {chroma_base_dir}")

    # ==================== Create Agents ====================
    logger.info(f"\n[Step 3/4] Creating {len(profiles)} agents...")

    agent_args = []
    
    for profile in profiles:
        agent_id = profile["id"]
        profile_text = profile["profile_text"]  # Only Profile Summary section

        # Create agent (using only Profile Summary section)
        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,  # Only Profile Summary
            }
        )

    # Create ImplicitAssociationTestEnv
    iat_env = ImplicitAssociationTestEnv(agent_ids=actual_agent_ids)

    # Create CodeGenRouter
    env_router = CodeGenRouter(env_modules=[iat_env])

    # Generate world description
    world_description = await env_router.generate_world_description_from_tools()

    logger.info("--------------------------------")
    logger.info("World Description:")
    logger.info(world_description)
    logger.info("--------------------------------")

    # Actually initialize agents
    # Note: world_description will be automatically set in agent.init() from env_router
    agents = [PersonAgent(**args) for args in agent_args]

    # ==================== Run Experiment ====================
    logger.info(f"\n[Step 4/4] Running experiment ({TOTAL_STEPS} steps, {TIME_STEP_SECONDS} seconds per step)...")

    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=START_TIME,
    )
    await society.init()

    await society.run(num_steps=TOTAL_STEPS, tick=TIME_STEP_SECONDS)

    # ==================== Extract Results ====================
    logger.info(f"\n[Extracting Results]")
    
    # Get all responses from environment
    results = iat_env.get_results()
    
    logger.info(f"  [OK] Extracted results from {len(results)} agents")
    
    # Check if results are empty
    if not results:
        logger.error("  [ERROR] No results extracted! Possible reasons:")
        logger.error("    1. Agents did not call submit_trial_response to submit responses")
        logger.error("    2. Insufficient experiment runtime, agents did not complete any trials")
        logger.error("    3. Error occurred during environment initialization or execution")
        logger.error("  Suggestion: Check log files for detailed error information")
        await society.close()
        return
    
    # Count total responses
    total_responses = sum(len(responses) for responses in results.values())
    if total_responses == 0:
        logger.warning("  [WARNING] All agents did not submit any responses!")
        logger.warning("    Possible reasons:")
        logger.warning("    1. Agents did not correctly call submit_trial_response")
        logger.warning("    2. Insufficient experiment runtime")
        logger.warning("    3. Agents encountered errors and could not continue")
    else:
        logger.info(f"  [OK] Collected {total_responses} trial responses in total")
    
    # Check completion status
    for agent_id, responses in results.items():
        num_completed = len(responses)
        total_trials = iat_env.total_trials
        logger.info(f"  Agent {agent_id}: {num_completed}/{total_trials} trials completed")
        if num_completed == 0:
            logger.warning(f"    [WARNING] This agent did not complete any trials")
        elif num_completed < total_trials:
            logger.warning(f"    [WARNING] Not all trials completed (completion: {num_completed/total_trials:.1%})")
        else:
            # Calculate accuracy
            correct_count = sum(1 for r in responses if r.get("corr") == 1)
            accuracy = correct_count / num_completed if num_completed > 0 else 0
            avg_rt = sum(r.get("rt", 0) for r in responses) / num_completed if num_completed > 0 else 0
            logger.info(f"    [OK] Accuracy: {accuracy:.2%}, Average reaction time: {avg_rt:.3f}s")

    # ==================== Save Results ====================
    logger.info(f"\n[Saving Results]")
    
    output_dir = "iat_experiment_results"
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save as JSON (always save, even if empty, for debugging)
    json_file = os.path.join(output_dir, f"iat_results_{timestamp}.json")
    json_data = {
        "results": {
            agent_id: responses
            for agent_id, responses in results.items()
        },
        "metadata": {
            "num_agents": len(profiles),
            "actual_agent_ids": actual_agent_ids,
            "total_steps": TOTAL_STEPS,
            "time_step_seconds": TIME_STEP_SECONDS,
            "start_time": START_TIME.isoformat(),
            "timestamp": timestamp,
            "profiles_dir": profiles_dir,
            "profile_files": [p["filename"] for p in profiles],
            "total_trials_per_agent": iat_env.total_trials,
            "total_responses_collected": sum(len(responses) for responses in results.values()),
        }
    }
    
    try:
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        logger.info(f"  [OK] JSON results saved to: {json_file}")
        if json_data["metadata"]["total_responses_collected"] == 0:
            logger.warning(f"    [WARNING] JSON file is empty (no responses collected)")
    except Exception as e:
        logger.error(f"  [ERROR] Failed to save JSON file: {e}")
    
    # Save as CSV (aligned with original IAT data format)
    csv_file = os.path.join(output_dir, f"iat_results_{timestamp}.csv")
    
    # Prepare CSV data - flatten responses for each agent
    csv_rows = []
    for agent_id, responses in results.items():
        for response in responses:
            row = {
                "ID": agent_id,
                "trial_id": response.get("trial_id"),
                "block_code": response.get("block_code"),
                "stimuli": response.get("stimuli"),
                "identity": response.get("identity"),
                "valence": response.get("valence"),
                "left_label": response.get("left_label"),
                "right_label": response.get("right_label"),
                "correct_key": response.get("correct_key"),
                "Key_press": response.get("key_press"),
                "RT": response.get("rt"),
                "CORR": response.get("corr"),
            }
            csv_rows.append(row)
    
    # Write CSV
    if csv_rows:
        try:
            fieldnames = [
                "ID", "trial_id", "block_code", "stimuli", "identity", "valence",
                "left_label", "right_label", "correct_key", "Key_press", "RT", "CORR"
            ]
            with open(csv_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_rows)
            logger.info(f"  [OK] CSV results saved to: {csv_file} ({len(csv_rows)} rows)")
        except Exception as e:
            logger.error(f"  [ERROR] Failed to save CSV file: {e}")
    else:
        logger.warning(f"  [WARNING] No data to save to CSV file (all agents did not submit responses)")
        # Still create an empty CSV file with header for reference
        try:
            fieldnames = [
                "ID", "trial_id", "block_code", "stimuli", "identity", "valence",
                "left_label", "right_label", "correct_key", "Key_press", "RT", "CORR"
            ]
            with open(csv_file, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            logger.info(f"  [OK] Created empty CSV file (header only): {csv_file}")
        except Exception as e:
            logger.error(f"  [ERROR] Failed to create CSV file: {e}")
    
    # Print summary statistics
    logger.info(f"\n[Statistics]")
    all_rt = []
    all_corr = []
    for responses in results.values():
        for response in responses:
            all_rt.append(response.get("rt", 0))
            all_corr.append(response.get("corr", 0))
    
    if all_rt:
        import numpy as np
        logger.info(f"  - Total trials: {len(all_rt)}")
        logger.info(f"  - Average reaction time: {np.mean(all_rt):.3f}s")
        logger.info(f"  - Median reaction time: {np.median(all_rt):.3f}s")
        logger.info(f"  - Reaction time std dev: {np.std(all_rt):.3f}s")
        logger.info(f"  - Overall accuracy: {np.mean(all_corr):.2%}")

    await society.close()
    
    logger.info("\n" + "=" * 80)
    logger.info("Experiment completed!")
    logger.info("=" * 80)


if __name__ == "__main__":
    # Create log directory
    log_dir = "logs/experiment"
    os.makedirs(log_dir, exist_ok=True)
    
    # Setup log file path
    log_file = os.path.join(
        log_dir,
        f"iat_experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    
    setup_logging(
        log_file=log_file,
        log_level=logging.DEBUG,
    )
    
    # Example 1: Use default settings (load 5 agents starting from index 0)
    # asyncio.run(main(logger=get_logger(), num_agents=5, profile_start_idx=0))
    
    # Example 2: Specify participant IDs
    # asyncio.run(main(logger=get_logger(), participant_ids=[101, 102, 103, 104, 105]))
    
    # Example 3: Test run
    asyncio.run(main(
        logger=get_logger(),
        num_agents=20,
        num_steps=40,
        tick_seconds=150,
    ))

