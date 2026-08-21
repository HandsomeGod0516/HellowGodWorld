"""
Commons Tragedy 實驗相關的主程式
使用 PersonAgent 進行公地悲劇遊戲模擬
"""

# ruff: noqa: E402

import asyncio
import json
import logging
import os
import shutil
import re
from collections import defaultdict
from datetime import datetime
from dotenv import load_dotenv
import numpy as np

load_dotenv()

from agentsociety2.contrib.env.commons_tragedy import CommonsTragedyEnv
from agentsociety2.contrib.env.prisoners_dilemma import PrisonersDilemmaEnv
from agentsociety2.contrib.env.public_goods import PublicGoodsEnv
from agentsociety2.contrib.env.trust_game import TrustGameEnv
from agentsociety2.contrib.env.volunteer_dilemma import VolunteerDilemmaEnv
from agentsociety2.agent import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.society import AgentSociety
from agentsociety2.logger import setup_logging, get_logger


def _calculate_volunteer_dilemma_statistics(
    per_game_payoffs,
    all_game_round_choices,
    at_least_one_volunteer_per_round,
    agent_names,
    num_rounds,
    save_dir,
):
    """計算並列印Volunteer's Dilemma統計資訊"""
    try:
        # 計算總獎勵
        total_payoffs = {name: 0 for name in agent_names}
        for payoffs in per_game_payoffs:
            for name in agent_names:
                total_payoffs[name] += payoffs.get(name, 0)

        # 列印統計資訊
        print("\n===== 志願者困境統計 =====")
        print(f"總遊戲數: {len(per_game_payoffs)}")
        print(f"總輪數: {num_rounds}")

        # 1. 平均volunteer機率
        avg_volunteer_prob = 0
        if all_game_round_choices:
            all_choices = [
                choice
                for round_data in all_game_round_choices.values()
                for choice in round_data
            ]
            if all_choices:
                avg_volunteer_prob = np.mean(all_choices)
                print(f"\n平均Volunteer機率: {avg_volunteer_prob:.2%}")

        # 2. 至少有一個volunteer的頻率
        freq_at_least_one = 0
        if at_least_one_volunteer_per_round:
            all_standalone_status = [
                status
                for round_data in at_least_one_volunteer_per_round.values()
                for status in round_data
            ]
            if all_standalone_status:
                freq_at_least_one = np.mean(all_standalone_status)
                print(f"至少有一個Volunteer的頻率: {freq_at_least_one:.2%}")

        # 3. 平均volunteer數量
        avg_num_volunteers = 0
        if all_game_round_choices:
            avg_num_volunteers = np.mean(
                [np.mean(round_data) for round_data in all_game_round_choices.values()]
            )
            print(f"平均volunteer數量/輪: {avg_num_volunteers:.2f}")

        print("\n所有遊戲的總獎勵:")
        for name in agent_names:
            print(f"  {name}: {total_payoffs[name]:.2f} 積分")
        overall_total = sum(total_payoffs.values())
        print(f"  總體總計: {overall_total:.2f} 積分")

        # 建立統計字典
        statistics = {
            "total_games": len(per_game_payoffs),
            "total_rounds_per_game": num_rounds,
            "avg_volunteer_probability": float(avg_volunteer_prob),
            "freq_at_least_one_volunteer": float(freq_at_least_one),
            "avg_num_volunteers": float(avg_num_volunteers),
            "total_payoffs": total_payoffs,
            "overall_total_payoff": overall_total,
        }

        # 儲存統計資訊到檔案
        stats_file_path = os.path.join(save_dir, "statistics.json")
        try:
            with open(stats_file_path, "w", encoding="utf-8") as f:
                json.dump(statistics, f, indent=2, ensure_ascii=False, default=str)
            logging.info(f"統計資訊已儲存到 {stats_file_path}")
        except Exception as e:
            logging.error(f"儲存統計資訊失敗: {e}")

        return statistics

    except Exception as e:
        logging.error(f"計算統計資訊時出錯: {e}")
        return None


def _calculate_trust_game_statistics(
    per_game_payoffs,
    all_game_investments,
    all_game_returns,
    all_game_return_rates,
    agent_names,
    num_rounds,
    save_dir,
):
    """計算並列印Trust Game統計資訊"""
    try:
        # 計算總獎勵
        total_payoffs = {name: 0 for name in agent_names}
        for payoffs in per_game_payoffs:
            for name in agent_names:
                total_payoffs[name] += payoffs.get(name, 0)

        # 區分trustor和trustee
        trustor_names = [name for name in agent_names if "Trustor" in name]
        trustee_names = [name for name in agent_names if "Trustee" in name]

        # 計算平均投資和回報率
        avg_investments = {}
        for name in trustor_names:
            total_investment = 0
            count = 0
            for investments in all_game_investments:
                base_name = name.rsplit("_G", 1)[0] if "_G" in name else name
                for inv_name, inv_value in investments.items():
                    inv_base = (
                        inv_name.rsplit("_G", 1)[0] if "_G" in inv_name else inv_name
                    )
                    if inv_base == base_name:
                        total_investment += inv_value
                        count += 1
                        break
            if count > 0:
                avg_investments[name] = total_investment / count
            else:
                avg_investments[name] = 0

        # 計算平均回報率
        avg_return_rates = {name: 0 for name in trustee_names}
        return_rate_counts = {name: 0 for name in trustee_names}
        for return_rates in all_game_return_rates:
            for name in trustee_names:
                if name in return_rates:
                    avg_return_rates[name] += return_rates[name]
                    return_rate_counts[name] += 1

        for name in trustee_names:
            if return_rate_counts[name] > 0:
                avg_return_rates[name] /= return_rate_counts[name]

        # 列印統計資訊
        print("\n===== 信任遊戲統計 =====")
        print(f"總遊戲數: {len(per_game_payoffs)}")
        print(f"總輪數: {num_rounds}")

        print("\n每個trustor的平均投資:")
        for name in trustor_names:
            print(f"  {name}: {avg_investments[name]:.2f} 單位")

        print("\n每個trustee的平均回報率:")
        for name in trustee_names:
            print(f"  {name}: {avg_return_rates[name]:.1%}")

        print("\n所有遊戲的總獎勵:")
        for name in agent_names:
            print(f"  {name}: {total_payoffs[name]:.2f} 積分")
        overall_total = sum(total_payoffs.values())
        print(f"  總體總計: {overall_total:.2f} 積分")

        # 建立統計字典
        statistics = {
            "total_games": len(per_game_payoffs),
            "total_rounds_per_game": num_rounds,
            "avg_investments": avg_investments,
            "avg_return_rates": avg_return_rates,
            "total_payoffs": total_payoffs,
            "overall_total_payoff": overall_total,
        }

        # 儲存統計資訊到檔案
        stats_file_path = os.path.join(save_dir, "statistics.json")
        try:
            with open(stats_file_path, "w", encoding="utf-8") as f:
                json.dump(statistics, f, indent=2, ensure_ascii=False, default=str)
            logging.info(f"統計資訊已儲存到 {stats_file_path}")
        except Exception as e:
            logging.error(f"儲存統計資訊失敗: {e}")

        return statistics

    except Exception as e:
        logging.error(f"計算統計資訊時出錯: {e}")
        return None


def _calculate_public_goods_statistics(
    per_game_payoffs,
    all_game_round_contributions,
    public_pool_total_contributions_history_per_game,
    agent_names,
    num_rounds,
    initial_endowment,
    save_dir,
):
    """計算並列印Public Goods Game統計資訊"""
    try:
        # 計算總獎勵
        total_payoffs = {name: 0 for name in agent_names}
        for payoffs in per_game_payoffs:
            for name in agent_names:
                total_payoffs[name] += payoffs.get(name, 0)

        # 計算每輪平均貢獻
        avg_contributions_per_round = []
        for round_num in range(1, num_rounds + 1):
            if round_num in all_game_round_contributions:
                contributions = all_game_round_contributions[round_num]
                avg_contrib = np.mean(contributions)
                avg_contributions_per_round.append(avg_contrib)

        # 計算總體平均貢獻
        overall_avg_contribution = (
            np.mean(avg_contributions_per_round) if avg_contributions_per_round else 0
        )

        # 列印統計資訊
        print("\n===== 貢獻統計 =====")
        print(f"總遊戲數: {len(per_game_payoffs)}")
        print(f"總輪數: {num_rounds}")
        print(f"每個agent每輪平均貢獻: {overall_avg_contribution:.2f} 單位")

        print("\n輪次平均貢獻:")
        for round_num, avg_contrib in enumerate(avg_contributions_per_round, 1):
            print(f"  輪次 {round_num}: {avg_contrib:.2f} 單位/agent")

        print("\n所有遊戲的總獎勵:")
        for name in agent_names:
            print(f"  {name}: {total_payoffs[name]:.2f} 積分")
        overall_total = sum(total_payoffs.values())
        print(f"  總體總計: {overall_total:.2f} 積分")

        # 判斷合作水平
        if overall_avg_contribution <= initial_endowment * 0.2:
            cooperation_level = "顯著搭便車（低貢獻）"
        elif overall_avg_contribution >= initial_endowment * 0.8:
            cooperation_level = "顯著合作（高貢獻）"
        else:
            cooperation_level = "中等貢獻或貢獻衰減現象"
        print(f"\n合作水平: {cooperation_level}")

        # 建立統計字典
        statistics = {
            "total_games": len(per_game_payoffs),
            "total_rounds_per_game": num_rounds,
            "overall_average_contribution_per_agent_per_round": overall_avg_contribution,
            "round_average_contributions": {
                i + 1: avg for i, avg in enumerate(avg_contributions_per_round)
            },
            "total_payoffs": total_payoffs,
            "overall_total_payoff": overall_total,
            "cooperation_level": cooperation_level,
            "public_pool_total_contributions_history_per_game": public_pool_total_contributions_history_per_game,
        }

        # 儲存統計資訊到檔案
        stats_file_path = os.path.join(save_dir, "statistics.json")
        try:
            with open(stats_file_path, "w", encoding="utf-8") as f:
                json.dump(statistics, f, indent=2, ensure_ascii=False, default=str)
            logging.info(f"統計資訊已儲存到 {stats_file_path}")
        except Exception as e:
            logging.error(f"儲存統計資訊失敗: {e}")

        return statistics

    except Exception as e:
        logging.error(f"計算統計資訊時出錯: {e}")
        return None


def _calculate_prisoners_dilemma_statistics(
    per_game_payoffs,
    total_actions,
    round_action_counts,
    agent_names,
    num_rounds,
    save_dir,
):
    """計算並列印Prisoner's Dilemma統計資訊"""
    try:
        # 計算總獎勵
        total_payoffs = {name: 0 for name in agent_names}
        for payoffs in per_game_payoffs:
            for name in agent_names:
                total_payoffs[name] += payoffs.get(name, 0)

        # 計算合作率
        cooperation_counts = {name: 0 for name in agent_names}
        total_action_counts = {name: 0 for name in agent_names}

        for name in agent_names:
            for action in total_actions.get(name, []):
                total_action_counts[name] += 1
                if action == "Yes":
                    cooperation_counts[name] += 1

        cooperation_rates = {
            name: (
                (cooperation_counts[name] / total_action_counts[name] * 100)
                if total_action_counts[name] > 0
                else 0
            )
            for name in agent_names
        }

        # 計算每輪合作率
        round_cooperation_rates = {}
        for round_num in range(1, num_rounds + 1):
            round_cooperation_rates[round_num] = {}
            for name in agent_names:
                round_actions = round_action_counts.get(name, {}).get(round_num, [])
                if round_actions:
                    round_coop_count = sum(1 for a in round_actions if a == "Yes")
                    round_cooperation_rates[round_num][name] = (
                        round_coop_count / len(round_actions) * 100
                    )
                else:
                    round_cooperation_rates[round_num][name] = 0

        # 列印統計資訊
        print("\n===== 合作率統計 =====")
        print(f"總遊戲數: {len(per_game_payoffs)}")
        print(f"總輪數: {num_rounds}")

        print("\n總體合作率:")
        for name in agent_names:
            print(f"  {name}: {cooperation_rates[name]:.2f}%")

        print("\n輪次合作率:")
        for round_num in sorted(round_cooperation_rates.keys()):
            print(f"  輪次 {round_num}:")
            for name in agent_names:
                rate = round_cooperation_rates[round_num].get(name, 0)
                print(f"    {name}: {rate:.2f}%")

        print("\n所有遊戲的總獎勵:")
        for name in agent_names:
            print(f"  {name}: {total_payoffs[name]} 積分")
        overall_total = sum(total_payoffs.values())
        print(f"  總體總計: {overall_total} 積分")

        # 判斷合作水平
        avg_cooperation_rate = (
            sum(cooperation_rates.values()) / len(cooperation_rates)
            if cooperation_rates
            else 0
        )
        if avg_cooperation_rate <= 20:
            cooperation_level = "低合作（主要背叛）"
        elif avg_cooperation_rate >= 80:
            cooperation_level = "高合作（主要合作）"
        else:
            cooperation_level = "中等合作"
        print(f"\n合作水平: {cooperation_level}")

        # 建立統計字典
        statistics = {
            "total_games": len(per_game_payoffs),
            "total_rounds_per_game": num_rounds,
            "cooperation_rates": cooperation_rates,
            "round_cooperation_rates": round_cooperation_rates,
            "total_payoffs": total_payoffs,
            "overall_total_payoff": overall_total,
            "cooperation_level": cooperation_level,
        }

        # 儲存統計資訊到檔案
        stats_file_path = os.path.join(save_dir, "statistics.json")
        try:
            with open(stats_file_path, "w", encoding="utf-8") as f:
                json.dump(statistics, f, indent=2, ensure_ascii=False, default=str)
            logging.info(f"統計資訊已儲存到 {stats_file_path}")
        except Exception as e:
            logging.error(f"儲存統計資訊失敗: {e}")

        return statistics

    except Exception as e:
        logging.error(f"計算統計資訊時出錯: {e}")
        return None


def _calculate_commons_tragedy_statistics(
    per_game_payoffs,
    total_extractions,
    round_extractions,
    pool_resources_history,
    agent_names,
    save_dir,
):
    """計算並列印統計資訊"""
    try:
        # 計算總獎勵
        total_payoffs = {name: 0 for name in agent_names}
        for payoffs in per_game_payoffs:
            for name in agent_names:
                total_payoffs[name] += payoffs.get(name, 0)

        # 計算平均提取
        average_extraction = (
            sum(total_extractions) / len(total_extractions) if total_extractions else 0
        )

        # 計算每輪平均提取
        round_avg_extractions = {}
        for round_num, extractions in round_extractions.items():
            round_avg_extractions[round_num] = (
                sum(extractions) / len(extractions) if extractions else 0
            )

        # 列印統計資訊
        print("\n===== 提取統計 =====")
        print(f"總遊戲數: {len(per_game_payoffs)}")
        print(f"總輪數: {len(total_extractions) // len(agent_names)}")
        print(f"每個 agent 每輪平均提取: {average_extraction:.2f} 單位")

        print("\n輪次平均提取:")
        for round_num in sorted(round_avg_extractions.keys()):
            print(
                f"  輪次 {round_num}: {round_avg_extractions[round_num]:.2f} 單位/agent"
            )

        print("\n所有遊戲的總獎勵:")
        for name in agent_names:
            print(f"  {name}: {total_payoffs[name]} 積分")
        overall_total = sum(total_payoffs.values())
        print(f"  總體總計: {overall_total} 積分")

        # 建立統計字典
        statistics = {
            "total_rounds": len(total_extractions) // len(agent_names),
            "total_games": len(per_game_payoffs),
            "average_extraction_per_agent_per_round": average_extraction,
            "round_average_extractions": round_avg_extractions,
            "total_payoffs": total_payoffs,
            "overall_total_payoff": overall_total,
            "pool_resources_history": pool_resources_history,
        }

        # 儲存統計資訊到檔案
        stats_file_path = os.path.join(save_dir, "statistics.json")
        try:
            with open(stats_file_path, "w", encoding="utf-8") as f:
                json.dump(statistics, f, indent=2, ensure_ascii=False, default=str)
            logging.info(f"統計資訊已儲存到 {stats_file_path}")
        except Exception as e:
            logging.error(f"儲存統計資訊失敗: {e}")

        return statistics

    except Exception as e:
        logging.error(f"計算統計資訊時出錯: {e}")
        return None


async def main_commons_tragedy_with_person_agent(
    logger,
    num_agents: int = 4,
    num_games: int = 5,
    num_rounds: int = 10,
    initial_pool_resources: int = 100,
    max_extraction_per_agent: int = 10,
    profile_start_idx: int = 0,
):
    """
    執行 Commons Tragedy（公地悲劇）遊戲 - 使用 PersonAgent (帶 ReAct)

    這個版本使用 PersonAgent 而不是 CommonsTragedyAgent，
    可以展現完整的 ReAct 迴圈和記憶系統
    """
    logger.info("\n" + "=" * 80)
    logger.info("【Commons Tragedy Game with PersonAgent (公地悲劇-PersonAgent版)】")
    logger.info("=" * 80)
    logger.info("實驗設定：")
    logger.info(f"  - Agent 數量: {num_agents}")
    logger.info(f"  - 遊戲局數: {num_games}")
    logger.info(f"  - 每局輪數: {num_rounds}")
    logger.info(f"  - 初始資源池: {initial_pool_resources} 單位")
    logger.info(f"  - 最大提取量/agent/輪: {max_extraction_per_agent} 單位")
    logger.info("=" * 80)

    # ------- 載入 Profiles ====================
    logger.info("\n【步驟1】載入 profiles.json...")

    profiles_path = os.path.join(os.path.dirname(__file__), "profiles.json")
    if not os.path.exists(profiles_path):
        logger.error(f"  ❌ profiles.json 檔案不存在: {profiles_path}")
        return

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    logger.info(f"  ✓ 載入了 {len(profiles)} 個 agent profiles")

    # 限制 agent 數量
    if num_agents > len(profiles):
        logger.warning(
            f"  ⚠ 請求的 agent 數量 ({num_agents}) 超過 profiles 數量 ({len(profiles)})，使用全部 {len(profiles)} 個"
        )
        num_agents = len(profiles)

    profiles_to_use = profiles[profile_start_idx : profile_start_idx + num_agents]
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")

    # ------- 初始化記憶體儲存 ====================
    logger.info("\n【步驟2】初始化記憶體儲存...")

    chroma_base_dir = "/tmp/chroma_memories_commons_tragedy"
    if os.path.exists(chroma_base_dir):
        shutil.rmtree(chroma_base_dir)
    os.makedirs(chroma_base_dir, exist_ok=True)

    # ------- 建立結果目錄 -------
    base_result_dir = "result_commons_tragedy_person_agent"
    os.makedirs(base_result_dir, exist_ok=True)

    experiment_time = datetime.now().strftime("%m%d_%H%M%S_ToC_PA")
    experiment_result_dir = os.path.join(base_result_dir, f"result_{experiment_time}")
    os.makedirs(experiment_result_dir, exist_ok=True)
    logger.info(f"實驗結果將儲存到: {experiment_result_dir}")

    # 記錄實驗配置
    experiment_config = {
        "experiment_type": "commons_tragedy_person_agent",
        "experiment_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "num_agents": num_agents,
        "num_games": num_games,
        "num_rounds_per_game": num_rounds,
        "initial_pool_resources": initial_pool_resources,
        "max_extraction_per_agent": max_extraction_per_agent,
        "result_dir": experiment_result_dir,
    }

    config_path = os.path.join(experiment_result_dir, "experiment_config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(experiment_config, f, indent=2, ensure_ascii=False)
        logger.info(f"實驗配置已儲存到: {config_path}")
    except Exception as e:
        logger.error(f"儲存實驗配置失敗: {e}")

    # ------- 建立 Agents ====================
    logger.info(f"\n【步驟3】建立 {num_agents} 個 PersonAgent...")

    agent_args = []
    date_time_str = datetime.now().strftime("%Y%m%d%H%M%S")

    for profile in profiles_to_use:
        agent_id = profile["id"]

        # 為每個 agent 建立獨立的 chroma 路徑
        agent_chroma_path = os.path.join(
            chroma_base_dir, f"agent_{agent_id}_{date_time_str}"
        )
        os.makedirs(agent_chroma_path, exist_ok=True)

        # 建立 Agent 特定的 memory 配置
        agent_memory_config = {
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": f"agent_{agent_id}_memories",
                    "path": agent_chroma_path,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "qwen2.5-14b-instruct",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "bge-m3",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                    "embedding_dims": 1024,
                },
            },
        }

        # 構建個人資料字串 - 針對 Commons Tragedy 遊戲最佳化
        profile_text = (
            f"My name is Agent-{agent_id}. "
            f"I am participating in a Tragedy of the Commons game. "
            f"My goal is to maximize my personal resource extraction over {num_rounds} rounds. "
            f"I can see other agents' behaviors and the state of the shared resource pool. "
            f"I need to make wise decisions to balance personal gain with resource preservation."
        )

        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,
                "memory_config": agent_memory_config,
                "world_description": f"You are playing a Tragedy of the Commons game with {num_agents-1} other players. The game has {num_rounds} rounds. Initial pool: {initial_pool_resources} units. Max extraction per round: {max_extraction_per_agent} units.",
                "max_plan_steps": 3,  # 限制Plan步驟數：查詢資源、做決策、提交
            }
        )

    # ------- 建立環境和 AgentSociety ====================
    logger.info("\n【步驟4】初始化環境和 AgentSociety...")

    # 統計變數
    total_extractions = []
    round_extractions = defaultdict(list)
    per_game_payoffs = []
    total_payoffs = defaultdict(int)
    pool_resources_history_per_game = []

    # ------- 遊戲迴圈 -------
    for game_num in range(1, num_games + 1):
        print(f"\n================ 遊戲 {game_num}/{num_games} ===============\n")
        logger.info(f"開始遊戲 {game_num}/{num_games}")

        # 建立環境模組
        env_module = CommonsTragedyEnv(
            num_agents=num_agents,
            initial_pool_resources=initial_pool_resources,
            max_extraction_per_agent=max_extraction_per_agent,
        )

        # 建立環境路由器
        env_router = CodeGenRouter(env_modules=[env_module])

        # 為這一局建立新的 agents
        agents = [PersonAgent(**args) for args in agent_args]

        # 公地悲劇實驗：初始化所有需求滿意度為 0.9
        for agent in agents:
            agent._satisfactions.satiety = 0.9
            agent._satisfactions.energy = 0.9
            agent._satisfactions.safety = 0.9
            agent._satisfactions.social = 0.9

        # 建立 AgentSociety
        start_time = datetime.now()
        society = None
        try:
            society = AgentSociety(
                agents=agents, env_router=env_router, start_t=start_time
            )
            await society.init()

            log_records = []
            current_game_pool_history = []
            game_payoffs = {f"Agent-{agent_id}": 0 for agent_id in actual_agent_ids}

            # ------- 輪次迴圈 -------
            for round_num in range(1, num_rounds + 1):
                print(f"\n--- 輪次 {round_num}/{num_rounds} (遊戲 {game_num}) ---")

                # 獲取當前輪次前的資源池
                ctx = {}
                ctx, pool_response = await env_router.ask(
                    ctx,
                    "Please call get_pool_resources() to get the current pool resources.",
                    readonly=True,
                )

                # 解析資源池
                pool_before = initial_pool_resources
                try:
                    json_match = re.search(r"\{[^}]+\}", pool_response)
                    if json_match:
                        data = json.loads(json_match.group(0))
                        if isinstance(data, dict):
                            pool_before = data.get("current_pool_resources", initial_pool_resources)
                except Exception:
                    pass

                print(f"輪次開始時資源池: {pool_before} 單位")
                logger.info(
                    f"遊戲 {game_num} 輪次 {round_num} 開始，資源池: {pool_before} 單位"
                )

                try:
                    # 執行一步 - PersonAgent 將透過 ReAct 迴圈完成決策和提交
                    await asyncio.wait_for(
                        society.step(tick=1),
                        timeout=300.0,  # 5 minute timeout per round
                    )

                    # 獲取輪次歷史
                    # 注意：由於 Round 的執行可能被延遲（等所有 Agent 都提交）
                    # 所以環境的 round_number 可能落後於 main_lab 的 round_num
                    # 因此我們直接取最新執行的 round，而不是按 round_num 匹配
                    latest_round = None
                    try:
                        if (
                            env_module.round_history
                            and len(env_module.round_history) > 0
                        ):
                            # 直接取最新執行的 round（列表最後一個）
                            # 而不是根據 round_num 搜尋，因為時序可能不同步
                            latest_round = env_module.round_history[-1]
                        else:
                            logger.warning(
                                f"遊戲 {game_num} 輪次 {round_num}: 沒有輪次歷史可用"
                            )
                    except Exception as e:
                        logger.warning(f"獲取輪次歷史失敗: {e}")

                    if latest_round:
                        actual_extractions = latest_round.get("extractions", {})
                        pool_after = latest_round.get("pool_after_round", pool_before)
                        payoffs = latest_round.get("payoffs", {})

                        # 更新統計資訊
                        for agent_id in actual_agent_ids:
                            # Agent name format: "Agent-{id}" (as specified in agent profile)
                            agent_name = f"Agent-{agent_id}"
                            actual_extraction = actual_extractions.get(agent_name, 0)
                            total_extractions.append(actual_extraction)
                            round_extractions[round_num].append(actual_extraction)
                            game_payoffs[f"Agent-{agent_id}"] += actual_extraction
                            total_payoffs[f"Agent-{agent_id}"] += actual_extraction

                        current_game_pool_history.append(pool_after)

                        # 構建日誌記錄
                        agent_round_data = {}
                        for agent_id in actual_agent_ids:
                            # Agent name format: "Agent-{id}" (as specified in agent profile)
                            agent_name = f"Agent-{agent_id}"
                            actual_extraction = actual_extractions.get(agent_name, 0)
                            payoff = payoffs.get(agent_name, 0)

                            agent_round_data[f"Agent-{agent_id}"] = {
                                "actual_extraction": actual_extraction,
                                "payoff": payoff,
                            }
                            print(
                                f"Agent-{agent_id}: 提取了 {actual_extraction} 單位 "
                                f"({payoff} 積分)"
                            )

                        log_records.append(
                            {
                                "round": round_num,
                                "pool_before_round": pool_before,
                                "pool_after_round": pool_after,
                                "agents_data": agent_round_data,
                                "timestamp": datetime.now().isoformat(),
                            }
                        )
                        print(f"輪次後資源池: {pool_after} 單位")
                        logger.info(
                            f"遊戲 {game_num} 輪次 {round_num} 結束，剩餘資源池: {pool_after} 單位"
                        )
                    else:
                        logger.warning(
                            f"遊戲 {game_num} 輪次 {round_num}: 無法從歷史記錄解析結果"
                        )

                except asyncio.TimeoutError:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行超時")
                    print(f"[錯誤] 輪次 {round_num} 執行超時，跳過此輪次")
                    continue
                except Exception as e:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行錯誤: {e}")
                    print(f"[錯誤] 輪次 {round_num} 執行錯誤: {str(e)}")
                    import traceback

                    traceback.print_exc()
                    continue

            # ------- 儲存每個遊戲的日誌 -------
            game_log_path = os.path.join(
                experiment_result_dir, f"game_{game_num}_logs.json"
            )
            try:
                with open(game_log_path, "w", encoding="utf-8") as f:
                    json.dump(log_records, f, indent=2, ensure_ascii=False)
                logger.info(f"遊戲 {game_num} 日誌已儲存到: {game_log_path}")
            except Exception as e:
                logger.error(f"儲存遊戲 {game_num} 日誌失敗: {e}")

            print(f"\n遊戲 {game_num} 完成。")

            # 儲存此遊戲的獎勵
            per_game_payoffs.append(game_payoffs.copy())
            total_game_payoff_sum = sum(game_payoffs.values())
            print(f"遊戲 {game_num} 獎勵:")
            for agent_id in actual_agent_ids:
                agent_key = f"Agent-{agent_id}"
                print(f"  {agent_key} = {game_payoffs[agent_key]} 積分")
            print(f"  此遊戲總積分 = {total_game_payoff_sum} 積分")
            pool_resources_history_per_game.append(current_game_pool_history)

        finally:
            # 清理
            if society:
                await society.close()

    # 所有遊戲完成後，列印總獎勵彙總
    print("\n========== 所有遊戲彙總 ==========")
    for idx, payoffs in enumerate(per_game_payoffs, 1):
        total_game_payoff_sum = sum(payoffs.values())
        print(
            f"遊戲 {idx}: "
            + ", ".join([f"{name}={pts} 積分" for name, pts in payoffs.items()])
            + f", 總計 = {total_game_payoff_sum} 積分"
        )

    print("\n所有遊戲的總獎勵:")
    overall_total_payoff_sum = sum(total_payoffs.values())
    for agent_key in sorted(total_payoffs.keys()):
        print(f"  {agent_key} 總計: {total_payoffs[agent_key]} 積分")
    print(f"  總體總計: {overall_total_payoff_sum} 積分")

    # 計算和列印統計資訊
    statistics = _calculate_commons_tragedy_statistics(
        per_game_payoffs,
        total_extractions,
        round_extractions,
        pool_resources_history_per_game,
        list(total_payoffs.keys()),
        experiment_result_dir,
    )

    if statistics:
        logger.info("實驗統計已計算並儲存")

    # 儲存總體實驗總結
    summary_path = os.path.join(experiment_result_dir, "experiment_summary.json")
    try:
        summary_data = experiment_config.copy()
        summary_data.update(
            {
                "total_payoffs": dict(total_payoffs),
                "per_game_payoffs": per_game_payoffs,
                "statistics": statistics,
            }
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        logger.info(f"實驗總結已儲存到: {summary_path}")
    except Exception as e:
        logger.error(f"儲存實驗總結失敗: {e}")

    print(f"\n實驗完成! 所有結果已儲存到: {experiment_result_dir}")
    logger.info("實驗執行完成")


async def main_prisoners_dilemma_with_person_agent(
    logger,
    num_games: int = 5,
    num_rounds: int = 10,
    payoff_cc: int = 3,
    payoff_cd: int = 0,
    payoff_dc: int = 5,
    payoff_dd: int = 1,
    profile_start_idx: int = 0,
):
    """
    執行 Prisoner's Dilemma（囚徒困境）遊戲 - 使用 PersonAgent (帶 ReAct)

    Prisoner's Dilemma 是一個2人遊戲：
    - 都合作 (CC): 各得3分
    - 一個合作，一個背叛 (CD): 合作者得0分，背叛者得5分
    - 都背叛 (DD): 各得1分
    """
    logger.info("\n" + "=" * 80)
    logger.info("【Prisoner's Dilemma Game with PersonAgent (囚徒困境-PersonAgent版)】")
    logger.info("=" * 80)
    logger.info("實驗設定：")
    logger.info("  - Agent 數量: 2 (Prisoner's Dilemma是2人遊戲)")
    logger.info(f"  - 遊戲局數: {num_games}")
    logger.info(f"  - 每局輪數: {num_rounds}")
    logger.info(
        f"  - 收益矩陣: CC={payoff_cc}, CD={payoff_cd}, DC={payoff_dc}, DD={payoff_dd}"
    )
    logger.info("=" * 80)

    # ------- 載入 Profiles ====================
    logger.info("\n【步驟1】載入 profiles.json...")

    profiles_path = os.path.join(os.path.dirname(__file__), "profiles.json")
    if not os.path.exists(profiles_path):
        logger.error(f"  ❌ profiles.json 檔案不存在: {profiles_path}")
        return

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    logger.info(f"  ✓ 載入了 {len(profiles)} 個 agent profiles")

    # 限制 agent 數量 (Prisoner's Dilemma 只需要2個)
    if len(profiles) < 2:
        logger.error("  ❌ profiles 數量不足，需要至少2個 agent")
        return

    profiles_to_use = profiles[profile_start_idx : profile_start_idx + 2]
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    agent_names = [f"Agent-{agent_id}" for agent_id in actual_agent_ids]
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")

    # ------- 初始化記憶體儲存 ====================
    logger.info("\n【步驟2】初始化記憶體儲存...")

    chroma_base_dir = "/tmp/chroma_memories_prisoners_dilemma"
    if os.path.exists(chroma_base_dir):
        shutil.rmtree(chroma_base_dir)
    os.makedirs(chroma_base_dir, exist_ok=True)

    # ------- 建立結果目錄 -------
    base_result_dir = "result_prisoners_dilemma_person_agent"
    os.makedirs(base_result_dir, exist_ok=True)

    experiment_time = datetime.now().strftime("%m%d_%H%M%S_PD_PA")
    experiment_result_dir = os.path.join(base_result_dir, f"result_{experiment_time}")
    os.makedirs(experiment_result_dir, exist_ok=True)
    logger.info(f"實驗結果將儲存到: {experiment_result_dir}")

    # 記錄實驗配置
    experiment_config = {
        "experiment_type": "prisoners_dilemma_person_agent",
        "experiment_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "num_games": num_games,
        "num_rounds_per_game": num_rounds,
        "payoff_cc": payoff_cc,
        "payoff_cd": payoff_cd,
        "payoff_dc": payoff_dc,
        "payoff_dd": payoff_dd,
        "result_dir": experiment_result_dir,
    }

    config_path = os.path.join(experiment_result_dir, "experiment_config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(experiment_config, f, indent=2, ensure_ascii=False)
        logger.info(f"實驗配置已儲存到: {config_path}")
    except Exception as e:
        logger.error(f"儲存實驗配置失敗: {e}")

    # ------- 建立 Agents ====================
    logger.info("\n【步驟3】建立 2 個 PersonAgent...")

    agent_args = []
    date_time_str = datetime.now().strftime("%Y%m%d%H%M%S")

    for profile in profiles_to_use:
        agent_id = profile["id"]

        # 為每個 agent 建立獨立的 chroma 路徑
        agent_chroma_path = os.path.join(
            chroma_base_dir, f"agent_{agent_id}_{date_time_str}"
        )
        os.makedirs(agent_chroma_path, exist_ok=True)

        # 建立 Agent 特定的 memory 配置
        agent_memory_config = {
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": f"agent_{agent_id}_memories",
                    "path": agent_chroma_path,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "qwen2.5-14b-instruct",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "bge-m3",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                    "embedding_dims": 1024,
                },
            },
        }

        # 構建個人資料字串 - 針對 Prisoner's Dilemma 遊戲最佳化
        profile_text = (
            f"My name is Agent-{agent_id}. "
            f"I am participating in a Prisoner's Dilemma game. "
            f"My goal is to maximize my payoff over {num_rounds} rounds. "
            f"Each round, I can choose to cooperate (Yes) or defect (No). "
            f"I need to decide wisely considering both my own gain and the other player's behavior."
        )

        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,
                "memory_config": agent_memory_config,
                "world_description": f"You are playing a Prisoner's Dilemma game with 1 other player. The game has {num_rounds} rounds. Payoff matrix: CC={payoff_cc}, CD={payoff_cd}, DC={payoff_dc}, DD={payoff_dd}.",
                "max_plan_steps": 2,  # 限制Plan步驟數：查詢收益矩陣、提交決策
            }
        )

    # ------- 建立環境和 AgentSociety ====================
    logger.info("\n【步驟4】初始化環境和 AgentSociety...")

    # 統計變數
    total_actions = {name: [] for name in agent_names}
    round_action_counts = {name: defaultdict(list) for name in agent_names}
    per_game_payoffs = []
    total_payoffs = {name: 0 for name in agent_names}

    # ------- 遊戲迴圈 -------
    for game_num in range(1, num_games + 1):
        print(f"\n================ 遊戲 {game_num}/{num_games} ===============\n")
        logger.info(f"開始遊戲 {game_num}/{num_games}")

        # 建立環境模組
        env_module = PrisonersDilemmaEnv(
            payoff_cc=payoff_cc,
            payoff_cd=payoff_cd,
            payoff_dc=payoff_dc,
            payoff_dd=payoff_dd,
        )

        # 建立環境路由器
        env_router = CodeGenRouter(env_modules=[env_module])

        # 為這一局建立新的 agents
        agents = [PersonAgent(**args) for args in agent_args]

        # 囚徒困境實驗：初始化所有需求滿意度為 0.9
        for agent in agents:
            agent._satisfactions.satiety = 0.9
            agent._satisfactions.energy = 0.9
            agent._satisfactions.safety = 0.9
            agent._satisfactions.social = 0.9

        # 建立 AgentSociety
        start_time = datetime.now()
        society = None
        try:
            society = AgentSociety(
                agents=agents, env_router=env_router, start_t=start_time
            )
            await society.init()

            log_records = []
            game_payoffs = {name: 0 for name in agent_names}

            # ------- 輪次迴圈 -------
            for round_num in range(1, num_rounds + 1):
                print(f"\n--- 輪次 {round_num}/{num_rounds} (遊戲 {game_num}) ---")

                try:
                    # 執行一步 - PersonAgent 將透過 ReAct 迴圈完成決策和提交
                    await asyncio.wait_for(
                        society.step(tick=1),
                        timeout=300.0,  # 5 minute timeout per round
                    )

                    # 獲取輪次歷史
                    latest_round = None
                    try:
                        if (
                            env_module.round_history
                            and len(env_module.round_history) > 0
                        ):
                            latest_round = env_module.round_history[-1]
                        else:
                            logger.warning(
                                f"遊戲 {game_num} 輪次 {round_num}: 沒有輪次歷史可用"
                            )
                    except Exception as e:
                        logger.warning(f"獲取輪次歷史失敗: {e}")

                    if latest_round:
                        agent_a_action = latest_round.get("agent_a_action", "")
                        agent_b_action = latest_round.get("agent_b_action", "")
                        agent_a_payoff = latest_round.get("agent_a_payoff", 0)
                        agent_b_payoff = latest_round.get("agent_b_payoff", 0)

                        # 更新統計資訊
                        game_payoffs[agent_names[0]] += agent_a_payoff
                        game_payoffs[agent_names[1]] += agent_b_payoff
                        total_payoffs[agent_names[0]] += agent_a_payoff
                        total_payoffs[agent_names[1]] += agent_b_payoff

                        # 記錄動作
                        if agent_a_action in ["Yes", "No"]:
                            total_actions[agent_names[0]].append(agent_a_action)
                            round_action_counts[agent_names[0]][round_num].append(
                                agent_a_action
                            )
                        if agent_b_action in ["Yes", "No"]:
                            total_actions[agent_names[1]].append(agent_b_action)
                            round_action_counts[agent_names[1]][round_num].append(
                                agent_b_action
                            )

                        # 構建日誌記錄
                        log_records.append(
                            {
                                "round": round_num,
                                agent_names[0]: {
                                    "action": agent_a_action,
                                    "payoff": agent_a_payoff,
                                    "cumulative_payoff": game_payoffs[agent_names[0]],
                                },
                                agent_names[1]: {
                                    "action": agent_b_action,
                                    "payoff": agent_b_payoff,
                                    "cumulative_payoff": game_payoffs[agent_names[1]],
                                },
                                "timestamp": datetime.now().isoformat(),
                            }
                        )

                        print(
                            f"{agent_names[0]}: {agent_a_action} ({agent_a_payoff} 積分, 累計: {game_payoffs[agent_names[0]]} 積分)"
                        )
                        print(
                            f"{agent_names[1]}: {agent_b_action} ({agent_b_payoff} 積分, 累計: {game_payoffs[agent_names[1]]} 積分)"
                        )
                        logger.info(
                            f"遊戲 {game_num} 輪次 {round_num} 結束, {agent_names[0]}: {agent_a_action} ({agent_a_payoff} 積分), {agent_names[1]}: {agent_b_action} ({agent_b_payoff} 積分)"
                        )
                    else:
                        logger.warning(
                            f"遊戲 {game_num} 輪次 {round_num}: 無法從歷史記錄解析結果"
                        )

                except asyncio.TimeoutError:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行超時")
                    print(f"[錯誤] 輪次 {round_num} 執行超時，跳過此輪次")
                    continue
                except Exception as e:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行錯誤: {e}")
                    print(f"[錯誤] 輪次 {round_num} 執行錯誤: {str(e)}")
                    import traceback

                    traceback.print_exc()
                    continue

            # ------- 儲存每個遊戲的日誌 -------
            game_log_path = os.path.join(
                experiment_result_dir, f"game_{game_num}_logs.json"
            )
            try:
                with open(game_log_path, "w", encoding="utf-8") as f:
                    json.dump(log_records, f, indent=2, ensure_ascii=False)
                logger.info(f"遊戲 {game_num} 日誌已儲存到: {game_log_path}")
            except Exception as e:
                logger.error(f"儲存遊戲 {game_num} 日誌失敗: {e}")

            print(f"\n遊戲 {game_num} 完成。")

            # 儲存此遊戲的獎勵
            per_game_payoffs.append(game_payoffs.copy())
            total_game_payoff_sum = sum(game_payoffs.values())
            print(f"遊戲 {game_num} 獎勵:")
            for name in agent_names:
                print(f"  {name} = {game_payoffs[name]} 積分")
            print(f"  此遊戲總積分 = {total_game_payoff_sum} 積分")

        finally:
            # 清理
            if society:
                await society.close()

    # 所有遊戲完成後，列印總獎勵彙總
    print("\n========== 所有遊戲彙總 ==========")
    for idx, payoffs in enumerate(per_game_payoffs, 1):
        total_game_payoff_sum = sum(payoffs.values())
        print(
            f"遊戲 {idx}: "
            + ", ".join([f"{name}={pts} 積分" for name, pts in payoffs.items()])
            + f", 總計 = {total_game_payoff_sum} 積分"
        )

    print("\n所有遊戲的總獎勵:")
    overall_total_payoff_sum = sum(total_payoffs.values())
    for name in agent_names:
        print(f"  {name} 總計: {total_payoffs[name]} 積分")
    print(f"  總體總計: {overall_total_payoff_sum} 積分")

    # 計算和列印統計資訊
    statistics = _calculate_prisoners_dilemma_statistics(
        per_game_payoffs,
        total_actions,
        round_action_counts,
        agent_names,
        num_rounds,
        experiment_result_dir,
    )

    if statistics:
        logger.info("實驗統計已計算並儲存")

    # 儲存總體實驗總結
    summary_path = os.path.join(experiment_result_dir, "experiment_summary.json")
    try:
        summary_data = experiment_config.copy()
        summary_data.update(
            {
                "total_payoffs": total_payoffs,
                "per_game_payoffs": per_game_payoffs,
                "statistics": statistics,
            }
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        logger.info(f"實驗總結已儲存到: {summary_path}")
    except Exception as e:
        logger.error(f"儲存實驗總結失敗: {e}")

    print(f"\n實驗完成! 所有結果已儲存到: {experiment_result_dir}")
    logger.info("實驗執行完成")


async def main_public_goods_with_person_agent(
    logger,
    num_agents: int = 4,
    num_games: int = 5,
    num_rounds: int = 10,
    initial_endowment: int = 20,
    public_pool_multiplier: float = 1.6,
    profile_start_idx: int = 0,
):
    """
    執行 Public Goods Game（公共物品遊戲）- 使用 PersonAgent (帶 ReAct)

    這個版本使用 PersonAgent 而不是 PublicGoodsAgent，
    可以展現完整的 ReAct 迴圈和記憶系統
    """
    logger.info("\n" + "=" * 80)
    logger.info("【Public Goods Game with PersonAgent (公共物品遊戲-PersonAgent版)】")
    logger.info("=" * 80)
    logger.info("實驗設定：")
    logger.info(f"  - Agent 數量: {num_agents}")
    logger.info(f"  - 遊戲局數: {num_games}")
    logger.info(f"  - 每局輪數: {num_rounds}")
    logger.info(f"  - 初始稟賦: {initial_endowment} 單位")
    logger.info(f"  - 公共池乘數: {public_pool_multiplier}")
    logger.info("=" * 80)

    # ------- 載入 Profiles ====================
    logger.info("\n【步驟1】載入 profiles.json...")

    profiles_path = os.path.join(os.path.dirname(__file__), "profiles.json")
    if not os.path.exists(profiles_path):
        logger.error(f"  ❌ profiles.json 檔案不存在: {profiles_path}")
        return

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    logger.info(f"  ✓ 載入了 {len(profiles)} 個 agent profiles")

    # 限制 agent 數量
    if num_agents > len(profiles):
        logger.warning(
            f"  ⚠ 請求的 agent 數量 ({num_agents}) 超過 profiles 數量 ({len(profiles)})，使用全部 {len(profiles)} 個"
        )
        num_agents = len(profiles)

    profiles_to_use = profiles[profile_start_idx : profile_start_idx + num_agents]
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    agent_names = [f"Agent-{agent_id}" for agent_id in actual_agent_ids]
    # 建立ID到名字的對映，用於處理LLM可能生成的不同格式的agent_name
    agent_id_to_name = {
        str(agent_id): f"Agent-{agent_id}" for agent_id in actual_agent_ids
    }
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")
    logger.info(f"  ✓ Agent ID到名字對映: {agent_id_to_name}")

    # ------- 初始化記憶體儲存 ====================
    logger.info("\n【步驟2】初始化記憶體儲存...")

    chroma_base_dir = "/tmp/chroma_memories_public_goods"
    if os.path.exists(chroma_base_dir):
        shutil.rmtree(chroma_base_dir)
    os.makedirs(chroma_base_dir, exist_ok=True)

    # ------- 建立結果目錄 -------
    base_result_dir = "result_public_goods_person_agent"
    os.makedirs(base_result_dir, exist_ok=True)

    experiment_time = datetime.now().strftime("%m%d_%H%M%S_PG_PA")
    experiment_result_dir = os.path.join(base_result_dir, f"result_{experiment_time}")
    os.makedirs(experiment_result_dir, exist_ok=True)
    logger.info(f"實驗結果將儲存到: {experiment_result_dir}")

    # 記錄實驗配置
    experiment_config = {
        "experiment_type": "public_goods_person_agent",
        "experiment_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "num_agents": num_agents,
        "num_games": num_games,
        "num_rounds_per_game": num_rounds,
        "initial_endowment": initial_endowment,
        "public_pool_multiplier": public_pool_multiplier,
        "result_dir": experiment_result_dir,
    }

    config_path = os.path.join(experiment_result_dir, "experiment_config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(experiment_config, f, indent=2, ensure_ascii=False)
        logger.info(f"實驗配置已儲存到: {config_path}")
    except Exception as e:
        logger.error(f"儲存實驗配置失敗: {e}")

    # ------- 建立 Agents ====================
    logger.info(f"\n【步驟3】建立 {num_agents} 個 PersonAgent...")

    agent_args = []
    date_time_str = datetime.now().strftime("%Y%m%d%H%M%S")

    for profile in profiles_to_use:
        agent_id = profile["id"]

        # 為每個 agent 建立獨立的 chroma 路徑
        agent_chroma_path = os.path.join(
            chroma_base_dir, f"agent_{agent_id}_{date_time_str}"
        )
        os.makedirs(agent_chroma_path, exist_ok=True)

        # 建立 Agent 特定的 memory 配置
        agent_memory_config = {
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": f"agent_{agent_id}_memories",
                    "path": agent_chroma_path,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "qwen2.5-14b-instruct",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "bge-m3",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                    "embedding_dims": 1024,
                },
            },
        }

        # 構建個人資料字串 - 針對 Public Goods Game 遊戲最佳化
        profile_text = (
            f"My name is Agent-{agent_id}. "
            f"I am participating in a Public Goods Game. "
            f"My goal is to maximize my payoff over {num_rounds} rounds. "
            f"Each round, I receive an endowment and decide how much to contribute to a public pool. "
            f"The pool is multiplied and then divided equally among all players. "
            f"I need to balance personal gain with group cooperation."
        )

        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,
                "memory_config": agent_memory_config,
                "world_description": f"You are playing a Public Goods Game with {num_agents-1} other players. The game has {num_rounds} rounds. Each round endowment: {initial_endowment} units. Public pool multiplier: {public_pool_multiplier}x.",
                "max_plan_steps": 3,  # 限制Plan步驟數：查詢歷史、做決策、提交貢獻
            }
        )

    # ------- 建立環境和 AgentSociety ====================
    logger.info("\n【步驟4】初始化環境和 AgentSociety...")

    # 統計變數
    all_game_round_contributions = defaultdict(list)
    per_game_payoffs = []
    total_payoffs = {name: 0 for name in agent_names}
    public_pool_total_contributions_history_per_game = []

    # ------- 遊戲迴圈 -------
    for game_num in range(1, num_games + 1):
        print(f"\n================ 遊戲 {game_num}/{num_games} ===============\n")
        logger.info(f"開始遊戲 {game_num}/{num_games}")

        # 建立環境模組
        env_module = PublicGoodsEnv(
            num_agents=num_agents,
            initial_endowment=initial_endowment,
            public_pool_multiplier=public_pool_multiplier,
        )

        # 建立環境路由器
        env_router = CodeGenRouter(env_modules=[env_module])

        # 為這一局建立新的 agents
        agents = [PersonAgent(**args) for args in agent_args]

        # 公共物品遊戲實驗：初始化所有需求滿意度為 0.9
        for agent in agents:
            agent._satisfactions.satiety = 0.9
            agent._satisfactions.energy = 0.9
            agent._satisfactions.safety = 0.9
            agent._satisfactions.social = 0.9

        # 建立 AgentSociety
        start_time = datetime.now()
        society = None
        try:
            society = AgentSociety(
                agents=agents, env_router=env_router, start_t=start_time
            )
            await society.init()

            log_records = []
            current_game_total_contributions = []
            game_payoffs = {name: 0 for name in agent_names}

            # ------- 輪次迴圈 -------
            for round_num in range(1, num_rounds + 1):
                print(f"\n--- 輪次 {round_num}/{num_rounds} (遊戲 {game_num}) ---")

                try:
                    # 執行一步 - PersonAgent 將透過 ReAct 迴圈完成決策和提交
                    await asyncio.wait_for(
                        society.step(tick=1),
                        timeout=300.0,  # 5 minute timeout per round
                    )

                    # 獲取輪次歷史
                    latest_round = None
                    try:
                        if (
                            env_module.round_history
                            and len(env_module.round_history) > 0
                        ):
                            latest_round = env_module.round_history[-1]
                        else:
                            logger.warning(
                                f"遊戲 {game_num} 輪次 {round_num}: 沒有輪次歷史可用"
                            )
                    except Exception as e:
                        logger.warning(f"獲取輪次歷史失敗: {e}")

                    if latest_round:
                        total_contribution = latest_round.get("total_contribution", 0)
                        public_pool_gain = latest_round.get("public_pool_gain", 0.0)
                        contributions = latest_round.get("contributions", {})
                        payoffs = latest_round.get("payoffs", {})

                        # 計算每個agent的收益
                        gain_per_agent = (
                            public_pool_gain / num_agents if num_agents > 0 else 0
                        )

                        # 更新統計資訊
                        for agent_name in agent_names:
                            payoff = payoffs.get(agent_name, 0.0)
                            game_payoffs[agent_name] += payoff
                            total_payoffs[agent_name] += payoff

                        # 記錄該輪次的總貢獻
                        current_game_total_contributions.append(total_contribution)

                        # 記錄每個agent的貢獻
                        for agent_name in agent_names:
                            contribution = contributions.get(agent_name, 0)
                            all_game_round_contributions[round_num].append(contribution)

                        # 構建日誌記錄
                        agent_round_data = {}
                        for agent_name in agent_names:
                            contribution = contributions.get(agent_name, 0)
                            payoff = payoffs.get(agent_name, 0.0)

                            agent_round_data[agent_name] = {
                                "contribution": contribution,
                                "payoff": payoff,
                                "cumulative_payoff": game_payoffs[agent_name],
                            }
                            print(
                                f"{agent_name}: 貢獻了 {contribution} 單位, "
                                f"獲得 {payoff:.2f} 積分（累計: {game_payoffs[agent_name]:.2f} 積分）"
                            )

                        log_records.append(
                            {
                                "round": round_num,
                                "total_contribution": total_contribution,
                                "public_pool_gain": public_pool_gain,
                                "gain_per_agent": gain_per_agent,
                                "agents_data": agent_round_data,
                                "timestamp": datetime.now().isoformat(),
                            }
                        )
                        print(f"總貢獻: {total_contribution} 單位")
                        print(f"公共池收益: {public_pool_gain:.2f} 積分")
                        print(f"每個agent從公共池獲得: {gain_per_agent:.2f} 積分")
                        logger.info(
                            f"遊戲 {game_num} 輪次 {round_num} 結束，總貢獻: {total_contribution}, 公共池收益: {public_pool_gain:.2f}"
                        )
                    else:
                        logger.warning(
                            f"遊戲 {game_num} 輪次 {round_num}: 無法從歷史記錄解析結果"
                        )

                except asyncio.TimeoutError:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行超時")
                    print(f"[錯誤] 輪次 {round_num} 執行超時，跳過此輪次")
                    continue
                except Exception as e:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行錯誤: {e}")
                    print(f"[錯誤] 輪次 {round_num} 執行錯誤: {str(e)}")
                    import traceback

                    traceback.print_exc()
                    continue

            # ------- 儲存每個遊戲的日誌 -------
            game_log_path = os.path.join(
                experiment_result_dir, f"game_{game_num}_logs.json"
            )
            try:
                with open(game_log_path, "w", encoding="utf-8") as f:
                    json.dump(log_records, f, indent=2, ensure_ascii=False)
                logger.info(f"遊戲 {game_num} 日誌已儲存到: {game_log_path}")
            except Exception as e:
                logger.error(f"儲存遊戲 {game_num} 日誌失敗: {e}")

            print(f"\n遊戲 {game_num} 完成。")

            # 儲存此遊戲的獎勵
            per_game_payoffs.append(game_payoffs.copy())
            total_game_payoff_sum = sum(game_payoffs.values())
            print(f"遊戲 {game_num} 獎勵:")
            for name in agent_names:
                print(f"  {name} = {game_payoffs[name]:.2f} 積分")
            print(f"  此遊戲總積分 = {total_game_payoff_sum:.2f} 積分")
            public_pool_total_contributions_history_per_game.append(
                current_game_total_contributions
            )

        finally:
            # 清理
            if society:
                await society.close()

    # 所有遊戲完成後，列印總獎勵彙總
    print("\n========== 所有遊戲彙總 ==========")
    for idx, payoffs in enumerate(per_game_payoffs, 1):
        total_game_payoff_sum = sum(payoffs.values())
        print(
            f"遊戲 {idx}: "
            + ", ".join([f"{name}={pts:.2f} 積分" for name, pts in payoffs.items()])
            + f", 總計 = {total_game_payoff_sum:.2f} 積分"
        )

    print("\n所有遊戲的總獎勵:")
    overall_total_payoff_sum = sum(total_payoffs.values())
    for name in agent_names:
        print(f"  {name} 總計: {total_payoffs[name]:.2f} 積分")
    print(f"  總體總計: {overall_total_payoff_sum:.2f} 積分")

    # 計算和列印統計資訊
    statistics = _calculate_public_goods_statistics(
        per_game_payoffs,
        all_game_round_contributions,
        public_pool_total_contributions_history_per_game,
        agent_names,
        num_rounds,
        initial_endowment,
        experiment_result_dir,
    )

    if statistics:
        logger.info("實驗統計已計算並儲存")

    # 儲存總體實驗總結
    summary_path = os.path.join(experiment_result_dir, "experiment_summary.json")
    try:
        summary_data = experiment_config.copy()
        summary_data.update(
            {
                "total_payoffs": total_payoffs,
                "per_game_payoffs": per_game_payoffs,
                "statistics": statistics,
            }
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"實驗總結已儲存到: {summary_path}")
    except Exception as e:
        logger.error(f"儲存實驗總結失敗: {e}")

    print(f"\n實驗完成! 所有結果已儲存到: {experiment_result_dir}")
    logger.info("實驗執行完成")


async def main_trust_game_with_person_agent(
    logger,
    num_pairs: int = 2,
    num_games: int = 2,
    num_rounds: int = 10,
    initial_funds: int = 10,
    multiplication_factor: float = 3.0,
    profile_start_idx: int = 0,
):
    """
    執行 Trust Game（信任遊戲）- 使用 PersonAgent (帶 ReAct)

    這個版本使用 PersonAgent 而不是 TrustGameAgent，
    可以展現完整的 ReAct 迴圈和記憶系統
    """
    logger.info("\n" + "=" * 80)
    logger.info("【Trust Game with PersonAgent (信任遊戲-PersonAgent版)】")
    logger.info("=" * 80)
    logger.info("實驗設定：")
    logger.info(f"  - Trustor-Trustee對數: {num_pairs}")
    logger.info(f"  - 遊戲局數: {num_games}")
    logger.info(f"  - 每局輪數: {num_rounds}")
    logger.info(f"  - 初始資金: {initial_funds} 單位")
    logger.info(f"  - 乘數因子: {multiplication_factor}x")
    logger.info("=" * 80)

    # ------- 載入 Profiles ====================
    logger.info("\n【步驟1】載入 profiles.json...")

    profiles_path = os.path.join(os.path.dirname(__file__), "profiles.json")
    if not os.path.exists(profiles_path):
        logger.error(f"  ❌ profiles.json 檔案不存在: {profiles_path}")
        return

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    logger.info(f"  ✓ 載入了 {len(profiles)} 個 agent profiles")

    # 需要的agent數量是pairs*2
    needed_agents = num_pairs * 2
    if len(profiles) < needed_agents:
        logger.warning(
            f"  ⚠ profiles 數量不足，需要 {needed_agents} 個 agent (對於 {num_pairs} 對)"
        )
        num_pairs = len(profiles) // 2

    profiles_to_use = profiles[profile_start_idx : profile_start_idx + num_pairs * 2]
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")

    # ------- 初始化記憶體儲存 ====================
    logger.info("\n【步驟2】初始化記憶體儲存...")

    chroma_base_dir = "/tmp/chroma_memories_trust_game"
    if os.path.exists(chroma_base_dir):
        shutil.rmtree(chroma_base_dir)
    os.makedirs(chroma_base_dir, exist_ok=True)

    # ------- 建立結果目錄 -------
    base_result_dir = "result_trust_game_person_agent"
    os.makedirs(base_result_dir, exist_ok=True)

    experiment_time = datetime.now().strftime("%m%d_%H%M%S_TG_PA")
    experiment_result_dir = os.path.join(base_result_dir, f"result_{experiment_time}")
    os.makedirs(experiment_result_dir, exist_ok=True)
    logger.info(f"實驗結果將儲存到: {experiment_result_dir}")

    # 記錄實驗配置
    experiment_config = {
        "experiment_type": "trust_game_person_agent",
        "experiment_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "num_pairs": num_pairs,
        "num_games": num_games,
        "num_rounds_per_game": num_rounds,
        "initial_funds": initial_funds,
        "multiplication_factor": multiplication_factor,
        "result_dir": experiment_result_dir,
    }

    config_path = os.path.join(experiment_result_dir, "experiment_config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(experiment_config, f, indent=2, ensure_ascii=False)
        logger.info(f"實驗配置已儲存到: {config_path}")
    except Exception as e:
        logger.error(f"儲存實驗配置失敗: {e}")

    # ------- 建立 Agents ====================
    logger.info(
        f"\n【步驟3】建立 {num_pairs} 對 PersonAgent (共 {num_pairs * 2} 個)..."
    )

    date_time_str = datetime.now().strftime("%Y%m%d%H%M%S")

    # 統計變數
    all_game_investments = []
    all_game_returns = []
    all_game_return_rates = []
    per_game_payoffs = []

    # ------- 遊戲迴圈 -------
    for game_num in range(1, num_games + 1):
        print(f"\n================ 遊戲 {game_num}/{num_games} ===============\n")
        logger.info(f"開始遊戲 {game_num}/{num_games}")

        # 建立環境模組
        env_module = TrustGameEnv(
            num_pairs=num_pairs,
            initial_funds=initial_funds,
            multiplication_factor=multiplication_factor,
        )

        # 建立環境路由器
        env_router = CodeGenRouter(env_modules=[env_module])

        # 為這一局建立新的 agents
        agent_args = []
        agent_names = []
        partner_mapping = {}

        for pair_idx in range(num_pairs):
            # Trustor
            trustor_id = profiles_to_use[pair_idx * 2]["id"]
            trustor_name = f"Agent-{trustor_id}_Trustor_G{game_num}"

            # Trustee
            trustee_id = profiles_to_use[pair_idx * 2 + 1]["id"]
            trustee_name = f"Agent-{trustee_id}_Trustee_G{game_num}"

            agent_names.append(trustor_name)
            agent_names.append(trustee_name)
            partner_mapping[trustor_name] = trustee_name
            partner_mapping[trustee_name] = trustor_name

            # 建立Trustor
            trustor_chroma_path = os.path.join(
                chroma_base_dir, f"agent_{trustor_id}_{date_time_str}"
            )
            os.makedirs(trustor_chroma_path, exist_ok=True)

            trustor_memory_config = {
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": f"agent_{trustor_id}_memories",
                        "path": trustor_chroma_path,
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": "qwen2.5-14b-instruct",
                        "api_key": os.getenv("INFINI_API_KEY"),
                        "openai_base_url": "https://llmapi.fiblab.net",
                    },
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": "bge-m3",
                        "api_key": os.getenv("INFINI_API_KEY"),
                        "openai_base_url": "https://llmapi.fiblab.net",
                        "embedding_dims": 1024,
                    },
                },
            }

            trustor_profile = (
                f"My name is {trustor_name}. "
                f"I am a Trustor in a Trust Game. "
                f"My goal is to maximize my payoff over {num_rounds} rounds. "
                f"Each round, I receive {initial_funds} coins and decide how much to send to my Trustee. "
                f"Whatever I send will be multiplied by {multiplication_factor} and then the Trustee decides how much to return to me. "
                f"I need to decide how much to trust."
            )

            agent_args.append(
                {
                    "id": trustor_id,
                    "profile": trustor_profile,
                    "memory_config": trustor_memory_config,
                    "world_description": f"You are a Trustor in a Trust Game with {num_pairs} pairs playing for {num_rounds} rounds. Initial funds: {initial_funds}. Multiplication factor: {multiplication_factor}x. IMPORTANT: When calling environment tools, use your FULL NAME '{trustor_name}' (not just your ID {trustor_id}). Example: submit_investment(trustor_name='{trustor_name}', investment=5).",
                    "max_plan_steps": 2,  # 限制Plan步驟數：查詢資料、提交投資
                }
            )

            # 建立Trustee
            trustee_chroma_path = os.path.join(
                chroma_base_dir, f"agent_{trustee_id}_{date_time_str}"
            )
            os.makedirs(trustee_chroma_path, exist_ok=True)

            trustee_memory_config = {
                "vector_store": {
                    "provider": "chroma",
                    "config": {
                        "collection_name": f"agent_{trustee_id}_memories",
                        "path": trustee_chroma_path,
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": "qwen2.5-14b-instruct",
                        "api_key": os.getenv("INFINI_API_KEY"),
                        "openai_base_url": "https://llmapi.fiblab.net",
                    },
                },
                "embedder": {
                    "provider": "openai",
                    "config": {
                        "model": "bge-m3",
                        "api_key": os.getenv("INFINI_API_KEY"),
                        "openai_base_url": "https://llmapi.fiblab.net",
                        "embedding_dims": 1024,
                    },
                },
            }

            trustee_profile = (
                f"My name is {trustee_name}. "
                f"I am a Trustee in a Trust Game. "
                f"My goal is to maximize my payoff over {num_rounds} rounds. "
                f"Each round, I receive coins from my Trustor (multiplied by {multiplication_factor}). "
                f"Then I decide how much of the received amount to return to my Trustor. "
                f"I need to decide whether to cooperate or act selfishly."
            )

            agent_args.append(
                {
                    "id": trustee_id,
                    "profile": trustee_profile,
                    "memory_config": trustee_memory_config,
                    "world_description": f"You are a Trustee in a Trust Game with {num_pairs} pairs playing for {num_rounds} rounds. Multiplication factor: {multiplication_factor}x. IMPORTANT: When calling environment tools, use your FULL NAME '{trustee_name}' (not just your ID {trustee_id}). Example: submit_return(trustee_name='{trustee_name}', return_amount=10).",
                    "max_plan_steps": 2,  # 限制Plan步驟數：查詢資料、提交回報
                }
            )

        # 設定環境的partner mapping
        env_module.set_partner_mapping(partner_mapping)

        # 建立 AgentSociety
        agents = [PersonAgent(**args) for args in agent_args]

        # 信任遊戲實驗：初始化所有需求滿意度為 0.9
        for agent in agents:
            agent._satisfactions.satiety = 0.9
            agent._satisfactions.energy = 0.9
            agent._satisfactions.safety = 0.9
            agent._satisfactions.social = 0.9

        start_time = datetime.now()
        society = None
        try:
            society = AgentSociety(
                agents=agents, env_router=env_router, start_t=start_time
            )
            await society.init()

            log_records = []
            game_payoffs = {name: 0 for name in agent_names}

            trustor_names = [name for name in agent_names if "Trustor" in name]
            trustee_names = [name for name in agent_names if "Trustee" in name]

            game_investments = {name: 0 for name in trustor_names}
            game_returns = {name: 0 for name in trustee_names}
            game_return_rates = {name: 0 for name in trustee_names}

            # ------- 輪次迴圈 -------
            for round_num in range(1, num_rounds + 1):
                print(f"\n--- 輪次 {round_num}/{num_rounds} (遊戲 {game_num}) ---")

                try:
                    # 執行一步
                    await asyncio.wait_for(society.step(tick=1), timeout=300.0)

                    # 獲取輪次歷史
                    latest_round = None
                    try:
                        if (
                            env_module.round_history
                            and len(env_module.round_history) > 0
                        ):
                            latest_round = env_module.round_history[-1]
                        else:
                            logger.warning(
                                f"遊戲 {game_num} 輪次 {round_num}: 沒有輪次歷史可用"
                            )
                    except Exception as e:
                        logger.warning(f"獲取輪次歷史失敗: {e}")

                    if latest_round:
                        trustor_investments = latest_round.get(
                            "trustor_investments", {}
                        )
                        trustee_returns = latest_round.get("trustee_returns", {})
                        payoffs = latest_round.get("payoffs", {})

                        # 更新統計資訊
                        for name in agent_names:
                            if name in payoffs:
                                game_payoffs[name] += payoffs[name]

                        # 記錄投資和回報
                        for trustor_name in trustor_names:
                            if trustor_name in trustor_investments:
                                game_investments[trustor_name] += trustor_investments[
                                    trustor_name
                                ]

                        for trustee_name in trustee_names:
                            if trustee_name in trustee_returns:
                                trustee_return = trustee_returns[trustee_name]
                                game_returns[trustee_name] += trustee_return

                                # 計算回報率
                                trustor_name = partner_mapping.get(trustee_name)
                                if trustor_name and trustor_name in trustor_investments:
                                    investment = trustor_investments[trustor_name]
                                    received = investment * multiplication_factor
                                    if received > 0:
                                        return_rate = trustee_return / received
                                    else:
                                        return_rate = 0
                                    game_return_rates[trustee_name] = return_rate

                        # 構建日誌記錄
                        round_log = {
                            "round": round_num,
                            "trustor_investments": trustor_investments,
                            "trustee_returns": trustee_returns,
                            "payoffs": payoffs,
                            "timestamp": datetime.now().isoformat(),
                        }
                        log_records.append(round_log)

                        # 列印輪次摘要
                        print(f"輪次 {round_num} 摘要:")
                        for trustor_name in trustor_names:
                            investment = trustor_investments.get(trustor_name, 0)
                            payoff = payoffs.get(trustor_name, 0)
                            print(
                                f"  {trustor_name}: 投資 {investment}, 收益 {payoff:.2f}"
                            )

                        for trustee_name in trustee_names:
                            return_amount = trustee_returns.get(trustee_name, 0)
                            payoff = payoffs.get(trustee_name, 0)
                            return_rate = game_return_rates.get(trustee_name, 0)
                            print(
                                f"  {trustee_name}: 返還 {return_amount}, 收益 {payoff:.2f}, 回報率 {return_rate:.1%}"
                            )

                        logger.info(f"遊戲 {game_num} 輪次 {round_num} 結束")
                    else:
                        logger.warning(
                            f"遊戲 {game_num} 輪次 {round_num}: 無法從歷史記錄解析結果"
                        )

                except asyncio.TimeoutError:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行超時")
                    print(f"[錯誤] 輪次 {round_num} 執行超時，跳過此輪次")
                    continue
                except Exception as e:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行錯誤: {e}")
                    print(f"[錯誤] 輪次 {round_num} 執行錯誤: {str(e)}")
                    import traceback

                    traceback.print_exc()
                    continue

            # ------- 儲存每個遊戲的日誌 -------
            game_log_path = os.path.join(
                experiment_result_dir, f"game_{game_num}_logs.json"
            )
            try:
                with open(game_log_path, "w", encoding="utf-8") as f:
                    json.dump(log_records, f, indent=2, ensure_ascii=False)
                logger.info(f"遊戲 {game_num} 日誌已儲存到: {game_log_path}")
            except Exception as e:
                logger.error(f"儲存遊戲 {game_num} 日誌失敗: {e}")

            print(f"\n遊戲 {game_num} 完成。")

            # 儲存此遊戲的資料
            per_game_payoffs.append(game_payoffs.copy())
            all_game_investments.append(game_investments.copy())
            all_game_returns.append(game_returns.copy())
            all_game_return_rates.append(game_return_rates.copy())

            print(f"遊戲 {game_num} 獎勵:")
            for agent_name in agent_names:
                print(f"  {agent_name}: {game_payoffs[agent_name]:.2f} 積分")
            total_game_payoff = sum(game_payoffs.values())
            print(f"  此遊戲總積分 = {total_game_payoff:.2f} 積分")

        finally:
            # 清理
            if society:
                await society.close()

    # 所有遊戲完成後，列印總獎勵彙總
    print("\n========== 所有遊戲彙總 ==========")
    for idx, payoffs in enumerate(per_game_payoffs, 1):
        total_game_payoff_sum = sum(payoffs.values())
        print(
            f"遊戲 {idx}: "
            + ", ".join([f"{name}={pts:.2f} 積分" for name, pts in payoffs.items()])
            + f", 總計 = {total_game_payoff_sum:.2f} 積分"
        )

    print("\n所有遊戲的總獎勵:")
    overall_total_payoff_sum = sum(
        sum(payoffs.values()) for payoffs in per_game_payoffs
    )
    for name in agent_names[: len(set(n.rsplit("_G", 1)[0] for n in agent_names))]:
        base_name = name.rsplit("_G", 1)[0] if "_G" in name else name
        total = sum(
            payoffs.get(n, 0)
            for payoffs in per_game_payoffs
            for n in payoffs
            if (n.rsplit("_G", 1)[0] if "_G" in n else n) == base_name
        )
        print(f"  {base_name} 總計: {total:.2f} 積分")
    print(f"  總體總計: {overall_total_payoff_sum:.2f} 積分")

    # 計算和列印統計資訊
    statistics = _calculate_trust_game_statistics(
        per_game_payoffs,
        all_game_investments,
        all_game_returns,
        all_game_return_rates,
        agent_names,
        num_rounds,
        experiment_result_dir,
    )

    if statistics:
        logger.info("實驗統計已計算並儲存")

    # 儲存總體實驗總結
    summary_path = os.path.join(experiment_result_dir, "experiment_summary.json")
    try:
        summary_data = experiment_config.copy()
        summary_data.update(
            {"per_game_payoffs": per_game_payoffs, "statistics": statistics}
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"實驗總結已儲存到: {summary_path}")
    except Exception as e:
        logger.error(f"儲存實驗總結失敗: {e}")

    print(f"\n實驗完成! 所有結果已儲存到: {experiment_result_dir}")
    logger.info("實驗執行完成")


async def main_volunteer_dilemma_with_person_agent(
    logger,
    num_agents: int = 4,
    num_games: int = 2,
    num_rounds: int = 10,
    benefit_b: int = 100,
    cost_c: int = 40,
    profile_start_idx: int = 0,
):
    """
    執行 Volunteer's Dilemma（志願者困境）- 使用 PersonAgent (帶 ReAct)

    這個版本使用 PersonAgent 而不是 VolunteerDilemmaAgent，
    可以展現完整的 ReAct 迴圈和記憶系統
    """
    logger.info("\n" + "=" * 80)
    logger.info("【Volunteer's Dilemma with PersonAgent (志願者困境-PersonAgent版)】")
    logger.info("=" * 80)
    logger.info("實驗設定：")
    logger.info(f"  - Agent 數量: {num_agents}")
    logger.info(f"  - 遊戲局數: {num_games}")
    logger.info(f"  - 每局輪數: {num_rounds}")
    logger.info(f"  - 利益 B: {benefit_b} 單位")
    logger.info(f"  - 成本 C: {cost_c} 單位")
    logger.info("=" * 80)

    # ------- 載入 Profiles ====================
    logger.info("\n【步驟1】載入 profiles.json...")

    profiles_path = os.path.join(os.path.dirname(__file__), "profiles.json")
    if not os.path.exists(profiles_path):
        logger.error(f"  ❌ profiles.json 檔案不存在: {profiles_path}")
        return

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    logger.info(f"  ✓ 載入了 {len(profiles)} 個 agent profiles")

    # 限制 agent 數量
    if num_agents > len(profiles):
        logger.warning(
            f"  ⚠ 請求的 agent 數量 ({num_agents}) 超過 profiles 數量 ({len(profiles)})，使用全部 {len(profiles)} 個"
        )
        num_agents = len(profiles)

    profiles_to_use = profiles[profile_start_idx : profile_start_idx + num_agents]
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    agent_names = [f"Agent-{agent_id}" for agent_id in actual_agent_ids]
    # 建立ID到名字的對映，用於處理LLM可能生成的不同格式的agent_name
    agent_id_to_name = {
        str(agent_id): f"Agent-{agent_id}" for agent_id in actual_agent_ids
    }
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")
    logger.info(f"  ✓ Agent ID到名字對映: {agent_id_to_name}")

    # ------- 初始化記憶體儲存 ====================
    logger.info("\n【步驟2】初始化記憶體儲存...")

    chroma_base_dir = "/tmp/chroma_memories_volunteer_dilemma"
    if os.path.exists(chroma_base_dir):
        shutil.rmtree(chroma_base_dir)
    os.makedirs(chroma_base_dir, exist_ok=True)

    # ------- 建立結果目錄 -------
    base_result_dir = "result_volunteer_dilemma_person_agent"
    os.makedirs(base_result_dir, exist_ok=True)

    experiment_time = datetime.now().strftime("%m%d_%H%M%S_VD_PA")
    experiment_result_dir = os.path.join(base_result_dir, f"result_{experiment_time}")
    os.makedirs(experiment_result_dir, exist_ok=True)
    logger.info(f"實驗結果將儲存到: {experiment_result_dir}")

    # 記錄實驗配置
    experiment_config = {
        "experiment_type": "volunteer_dilemma_person_agent",
        "experiment_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "num_agents": num_agents,
        "num_games": num_games,
        "num_rounds_per_game": num_rounds,
        "benefit_b": benefit_b,
        "cost_c": cost_c,
        "result_dir": experiment_result_dir,
    }

    config_path = os.path.join(experiment_result_dir, "experiment_config.json")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(experiment_config, f, indent=2, ensure_ascii=False)
        logger.info(f"實驗配置已儲存到: {config_path}")
    except Exception as e:
        logger.error(f"儲存實驗配置失敗: {e}")

    # ------- 建立 Agents ====================
    logger.info(f"\n【步驟3】建立 {num_agents} 個 PersonAgent...")

    agent_args = []
    date_time_str = datetime.now().strftime("%Y%m%d%H%M%S")

    for profile in profiles_to_use:
        agent_id = profile["id"]

        # 為每個 agent 建立獨立的 chroma 路徑
        agent_chroma_path = os.path.join(
            chroma_base_dir, f"agent_{agent_id}_{date_time_str}"
        )
        os.makedirs(agent_chroma_path, exist_ok=True)

        # 建立 Agent 特定的 memory 配置
        agent_memory_config = {
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": f"agent_{agent_id}_memories",
                    "path": agent_chroma_path,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "qwen2.5-14b-instruct",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": "bge-m3",
                    "api_key": os.getenv("INFINI_API_KEY"),
                    "openai_base_url": "https://llmapi.fiblab.net",
                    "embedding_dims": 1024,
                },
            },
        }

        # 構建個人資料字串 - 針對 Volunteer's Dilemma 遊戲最佳化
        profile_text = (
            f"My name is Agent-{agent_id}. "
            f"I am participating in a Volunteer's Dilemma game. "
            f"My goal is to maximize my payoff over {num_rounds} rounds. "
            f"Each round, I can choose to 'Volunteer' (incur cost C={cost_c}) or 'Stand by'. "
            f"If anyone volunteers, everyone gets benefit B={benefit_b}. "
            f"I need to decide whether to volunteer and help the group or free-ride."
        )

        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,
                "memory_config": agent_memory_config,
                "world_description": f"You are playing a Volunteer's Dilemma game with {num_agents-1} other players. The game has {num_rounds} rounds. Benefit B={benefit_b}, Cost C={cost_c}.",
                "max_plan_steps": 2,  # 限制Plan步驟數：只需提交選擇（Volunteer或Stand by）
            }
        )

    # ------- 建立環境和 AgentSociety ====================
    logger.info("\n【步驟4】初始化環境和 AgentSociety...")

    # 統計變數
    all_game_round_choices = defaultdict(list)
    at_least_one_volunteer_per_round = defaultdict(list)
    per_game_payoffs = []

    # ------- 遊戲迴圈 -------
    for game_num in range(1, num_games + 1):
        print(f"\n================ 遊戲 {game_num}/{num_games} ===============\n")
        logger.info(f"開始遊戲 {game_num}/{num_games}")

        # 建立環境模組
        env_module = VolunteerDilemmaEnv(
            num_agents=num_agents, benefit_b=benefit_b, cost_c=cost_c
        )

        # 建立環境路由器
        env_router = CodeGenRouter(env_modules=[env_module])

        # 為這一局建立新的 agents
        agents = [PersonAgent(**args) for args in agent_args]

        # 志願者困境實驗：初始化所有需求滿意度為 0.9
        for agent in agents:
            agent._satisfactions.satiety = 0.9
            agent._satisfactions.energy = 0.9
            agent._satisfactions.safety = 0.9
            agent._satisfactions.social = 0.9

        # 建立 AgentSociety
        start_time = datetime.now()
        society = None
        try:
            society = AgentSociety(
                agents=agents, env_router=env_router, start_t=start_time
            )
            await society.init()

            log_records = []
            game_payoffs = {name: 0 for name in agent_names}

            # ------- 輪次迴圈 -------
            for round_num in range(1, num_rounds + 1):
                print(f"\n--- 輪次 {round_num}/{num_rounds} (遊戲 {game_num}) ---")

                try:
                    # 執行一步 - PersonAgent 將透過 ReAct 迴圈完成決策和提交
                    await asyncio.wait_for(society.step(tick=1), timeout=300.0)

                    # 獲取輪次歷史 - 確保獲取的是當前輪的資料
                    latest_round = None
                    try:
                        if (
                            env_module.round_history
                            and len(env_module.round_history) > 0
                        ):
                            # 找到匹配當前round_num的round記錄
                            for r in reversed(env_module.round_history):
                                if r.get("round") == round_num:
                                    latest_round = r
                                    break
                            # 如果沒找到，使用最新的
                            if latest_round is None:
                                latest_round = env_module.round_history[-1]
                                logger.warning(
                                    f"遊戲 {game_num} 輪次 {round_num}: 沒有找到匹配的round記錄，使用最新: {latest_round.get('round')}"
                                )
                        else:
                            logger.warning(
                                f"遊戲 {game_num} 輪次 {round_num}: 沒有輪次歷史可用"
                            )
                    except Exception as e:
                        logger.warning(f"獲取輪次歷史失敗: {e}")

                    if latest_round:
                        choices = latest_round.get("choices", {})
                        num_volunteers = latest_round.get("num_volunteers", 0)
                        payoffs = latest_round.get("payoffs", {})
                        is_someone_volunteering = latest_round.get(
                            "is_someone_volunteering", False
                        )

                        # 除錯資訊 - 列印完整的round資料
                        print(
                            f"\n[DEBUG Round {round_num}] 環境返回的原始payoffs: {payoffs}"
                        )
                        print(f"[DEBUG Round {round_num}] Choices: {choices}")
                        print(
                            f"[DEBUG Round {round_num}] Volunteers: {num_volunteers}, Someone volunteering: {is_someone_volunteering}"
                        )
                        print(
                            f"[DEBUG Round {round_num}] Benefit B: {latest_round.get('benefit_b')}, Cost C: {latest_round.get('cost_c')}"
                        )
                        print(
                            f"[DEBUG Round {round_num}] Payoffs dict keys: {list(payoffs.keys())}"
                        )
                        print(
                            f"[DEBUG Round {round_num}] Payoffs dict values: {list(payoffs.values())}"
                        )

                        logger.debug(
                            f"Round {round_num}: choices={choices}, payoffs={payoffs}, volunteers={num_volunteers}"
                        )
                        logger.debug(
                            f"Round {round_num}: num_agents_submitted={latest_round.get('num_agents_submitted')}, benefit_b={latest_round.get('benefit_b')}, cost_c={latest_round.get('cost_c')}"
                        )

                        # 更新統計資訊和記錄payoff
                        # 由於LLM生成的程式碼可能使用多種不同格式的agent_name，
                        # 我們需要嘗試多種查詢方式來獲取正確的payoff
                        payoff_map = {}  # 儲存每個agent_name對應的payoff，用於後續記錄

                        for agent_name in agent_names:
                            payoff = 0
                            agent_id = agent_name.replace("Agent-", "")

                            # 嘗試多種可能的key格式（LLM可能生成的）
                            possible_keys = [
                                agent_name,  # "Agent-1"
                                agent_id,  # "1"
                                f"agent_{agent_id}",  # "agent_1"
                                f"Agent_{agent_id}",  # "Agent_1"
                                str(int(agent_id)),  # "1"（如果agent_id是字串）
                            ]

                            for possible_key in possible_keys:
                                if possible_key in payoffs:
                                    payoff = payoffs.get(possible_key, 0)
                                    break

                            payoff_map[agent_name] = payoff
                            game_payoffs[agent_name] += payoff

                        # 記錄選擇 (1=Volunteer, 0=Stand by)
                        numerical_choices = [
                            1 if choices.get(name) == "Volunteer" else 0
                            for name in agent_names
                        ]
                        all_game_round_choices[round_num].extend(numerical_choices)

                        # 記錄是否有volunteer
                        at_least_one_volunteer_per_round[round_num].append(
                            1 if is_someone_volunteering else 0
                        )

                        # 構建日誌記錄
                        agent_round_data = {}
                        for agent_name in agent_names:
                            agent_id = agent_name.replace("Agent-", "")

                            # 查詢choice - 嘗試多種可能的key格式
                            choice = "Stand by"  # 預設值
                            possible_choice_keys = [
                                agent_name,  # "Agent-1"
                                agent_id,  # "1"
                                f"agent_{agent_id}",  # "agent_1"
                                f"Agent_{agent_id}",  # "Agent_1"
                                str(int(agent_id)),  # "1"
                            ]
                            for possible_key in possible_choice_keys:
                                if possible_key in choices:
                                    choice = choices[possible_key]
                                    break

                            # 使用payoff_map中已經查詢過的payoff值
                            round_payoff = payoff_map.get(agent_name, 0)

                            agent_round_data[agent_name] = {
                                "choice": choice,
                                "payoff": round_payoff,
                                "cumulative_payoff": game_payoffs[agent_name],
                            }
                            print(
                                f"{agent_name}: 選擇 '{choices.get(agent_name, 'Stand by')}', "
                                f"本輪收益 {payoffs.get(agent_name, 0):.2f} 積分, "
                                f"累計收益 {game_payoffs[agent_name]:.2f} 積分"
                            )

                        log_records.append(
                            {
                                "round": round_num,
                                "num_volunteers": num_volunteers,
                                "is_someone_volunteering": is_someone_volunteering,
                                "agents_data": agent_round_data,
                                "timestamp": datetime.now().isoformat(),
                            }
                        )

                        print(
                            f"本輪志願者數: {num_volunteers}，是否有人志願: {is_someone_volunteering}"
                        )
                        logger.info(
                            f"遊戲 {game_num} 輪次 {round_num} 結束，志願者: {num_volunteers}"
                        )
                    else:
                        logger.warning(
                            f"遊戲 {game_num} 輪次 {round_num}: 無法從歷史記錄解析結果"
                        )

                except asyncio.TimeoutError:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行超時")
                    print(f"[錯誤] 輪次 {round_num} 執行超時，跳過此輪次")
                    continue
                except Exception as e:
                    logger.error(f"遊戲 {game_num} 輪次 {round_num} 執行錯誤: {e}")
                    print(f"[錯誤] 輪次 {round_num} 執行錯誤: {str(e)}")
                    import traceback

                    traceback.print_exc()
                    continue

            # ------- 儲存每個遊戲的日誌 -------
            game_log_path = os.path.join(
                experiment_result_dir, f"game_{game_num}_logs.json"
            )
            try:
                with open(game_log_path, "w", encoding="utf-8") as f:
                    json.dump(log_records, f, indent=2, ensure_ascii=False)
                logger.info(f"遊戲 {game_num} 日誌已儲存到: {game_log_path}")
            except Exception as e:
                logger.error(f"儲存遊戲 {game_num} 日誌失敗: {e}")

            print(f"\n遊戲 {game_num} 完成。")

            # 儲存此遊戲的獎勵
            per_game_payoffs.append(game_payoffs.copy())
            total_game_payoff_sum = sum(game_payoffs.values())
            print(f"遊戲 {game_num} 獎勵:")
            for name in agent_names:
                print(f"  {name} = {game_payoffs[name]:.2f} 積分")
            print(f"  此遊戲總積分 = {total_game_payoff_sum:.2f} 積分")

        finally:
            # 清理
            if society:
                await society.close()

    # 所有遊戲完成後，列印總獎勵彙總
    print("\n========== 所有遊戲彙總 ==========")
    for idx, payoffs in enumerate(per_game_payoffs, 1):
        total_game_payoff_sum = sum(payoffs.values())
        print(
            f"遊戲 {idx}: "
            + ", ".join([f"{name}={pts:.2f} 積分" for name, pts in payoffs.items()])
            + f", 總計 = {total_game_payoff_sum:.2f} 積分"
        )

    print("\n所有遊戲的總獎勵:")
    overall_total_payoff_sum = sum(
        sum(payoffs.values()) for payoffs in per_game_payoffs
    )
    for name in agent_names:
        total = sum(payoffs.get(name, 0) for payoffs in per_game_payoffs)
        print(f"  {name} 總計: {total:.2f} 積分")
    print(f"  總體總計: {overall_total_payoff_sum:.2f} 積分")

    # 計算和列印統計資訊
    statistics = _calculate_volunteer_dilemma_statistics(
        per_game_payoffs,
        all_game_round_choices,
        at_least_one_volunteer_per_round,
        agent_names,
        num_rounds,
        experiment_result_dir,
    )

    if statistics:
        logger.info("實驗統計已計算並儲存")

    # 儲存總體實驗總結
    summary_path = os.path.join(experiment_result_dir, "experiment_summary.json")
    try:
        summary_data = experiment_config.copy()
        summary_data.update(
            {"per_game_payoffs": per_game_payoffs, "statistics": statistics}
        )
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"實驗總結已儲存到: {summary_path}")
    except Exception as e:
        logger.error(f"儲存實驗總結失敗: {e}")

    print(f"\n實驗完成! 所有結果已儲存到: {experiment_result_dir}")
    logger.info("實驗執行完成")


async def run_all_games():
    """
    順序執行所有遊戲，確保每個遊戲之間完全隔離
    """
    print("\n" + "=" * 80)
    print("【開始執行所有遊戲 - 完全隔離模式】")
    print("=" * 80)

    games_info = [
        {
            "name": "Commons Tragedy（公地悲劇）",
            "num": 1,
            "agents": 4,
        },
        {
            "name": "Prisoner's Dilemma（囚徒困境）",
            "num": 2,
            "agents": 2,
        },
        {
            "name": "Public Goods Game（公共物品遊戲）",
            "num": 3,
            "agents": 4,
        },
        {
            "name": "Trust Game（信任遊戲）",
            "num": 4,
            "agents": 4,  # 2 pairs
        },
        {
            "name": "Volunteer's Dilemma（志願者困境）",
            "num": 5,
            "agents": 4,
        },
    ]

    total_start_time = datetime.now()

    for idx, game_info in enumerate(games_info, 1):
        print("\n" + "=" * 80)
        print(f"【遊戲 {idx}/5】{game_info['name']}")
        print(f"Agent 數量: {game_info['agents']}")
        print("=" * 80)

        game_start_time = datetime.now()

        try:
            if game_info["num"] == 1:
                # Commons Tragedy: 4 agents
                setup_logging(
                    log_file=f"game_logs/commons_tragedy_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
                    log_level=logging.DEBUG,
                )
                await main_commons_tragedy_with_person_agent(
                    logger=get_logger(),
                    num_agents=4,
                    num_games=5,
                    num_rounds=10,
                    initial_pool_resources=100,
                    max_extraction_per_agent=10,
                    profile_start_idx=0,
                )

            elif game_info["num"] == 2:
                # Prisoner's Dilemma: 2 agents
                setup_logging(
                    log_file=f"game_logs/prisoners_dilemma_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
                    log_level=logging.DEBUG,
                )
                await main_prisoners_dilemma_with_person_agent(
                    logger=get_logger(),
                    num_games=5,
                    num_rounds=10,
                    payoff_cc=3,
                    payoff_cd=0,
                    payoff_dc=5,
                    payoff_dd=1,
                    profile_start_idx=0,
                )

            elif game_info["num"] == 3:
                # Public Goods Game: 4 agents
                setup_logging(
                    log_file=f"game_logs/public_goods_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
                    log_level=logging.DEBUG,
                )
                await main_public_goods_with_person_agent(
                    logger=get_logger(),
                    num_agents=4,
                    num_games=5,
                    num_rounds=10,
                    initial_endowment=20,
                    public_pool_multiplier=1.6,
                    profile_start_idx=0,
                )

            elif game_info["num"] == 4:
                # Trust Game: 2 pairs (4 agents)
                setup_logging(
                    log_file=f"game_logs/trust_game_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
                    log_level=logging.DEBUG,
                )
                await main_trust_game_with_person_agent(
                    logger=get_logger(),
                    num_pairs=2,
                    num_games=5,
                    num_rounds=10,
                    initial_funds=10,
                    multiplication_factor=3.0,
                    profile_start_idx=0,
                )

            elif game_info["num"] == 5:
                # Volunteer's Dilemma: 4 agents
                setup_logging(
                    log_file=f"game_logs/volunteer_dilemma_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
                    log_level=logging.DEBUG,
                )
                await main_volunteer_dilemma_with_person_agent(
                    logger=get_logger(),
                    num_agents=4,
                    num_games=5,
                    num_rounds=10,
                    benefit_b=100,
                    cost_c=40,
                    profile_start_idx=0,
                )

            game_duration = datetime.now() - game_start_time
            print(f"\n✓ 遊戲 {idx} 完成 (耗時: {game_duration.total_seconds():.1f} 秒)")
            print("=" * 80)

            # 遊戲之間的延遲 - 確保完全隔離
            print("\n等待 5 秒進行遊戲隔離...")
            await asyncio.sleep(5)

        except Exception as e:
            print(f"\n✗ 遊戲 {idx} 執行出錯: {e}")
            import traceback

            traceback.print_exc()
            # 繼續執行下一個遊戲
            continue

    total_duration = datetime.now() - total_start_time
    print("\n" + "=" * 80)
    print("【所有遊戲執行完成】")
    print(
        f"總耗時: {total_duration.total_seconds():.1f} 秒 ({total_duration.total_seconds()/60:.1f} 分鐘)"
    )
    print("=" * 80)


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("【AgentSociety 遊戲實驗平臺】")
    print("=" * 80)
    print("\n請選擇要執行的遊戲型別:")
    print("  1. Commons Tragedy（公地悲劇）")
    print("  2. Prisoner's Dilemma（囚徒困境）")
    print("  3. Public Goods Game（公共物品遊戲）")
    print("  4. Trust Game（信任遊戲）")
    print("  5. Volunteer's Dilemma（志願者困境）")
    print("  6. 全部執行（依次執行所有遊戲，完全隔離）")
    print("=" * 80)

    choice = input("\n請輸入選擇 (1-6): ").strip()

    if choice == "1":
        setup_logging(
            log_file=f"game_logs/commons_tragedy_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
            log_level=logging.DEBUG,
        )

        asyncio.run(
            main_commons_tragedy_with_person_agent(
                logger=get_logger(),
                num_agents=4,
                num_games=5,
                num_rounds=10,
                initial_pool_resources=100,
                max_extraction_per_agent=10,
            )
        )
    elif choice == "2":
        setup_logging(
            log_file=f"game_logs/prisoners_dilemma_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
            log_level=logging.DEBUG,
        )

        asyncio.run(
            main_prisoners_dilemma_with_person_agent(
                logger=get_logger(),
                num_games=5,
                num_rounds=10,
                payoff_cc=3,
                payoff_cd=0,
                payoff_dc=5,
                payoff_dd=1,
            )
        )
    elif choice == "3":
        setup_logging(
            log_file=f"game_logs/public_goods_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
            log_level=logging.DEBUG,
        )

        asyncio.run(
            main_public_goods_with_person_agent(
                logger=get_logger(),
                num_agents=4,
                num_games=5,
                num_rounds=10,
                initial_endowment=20,
                public_pool_multiplier=1.6,
            )
        )
    elif choice == "4":
        setup_logging(
            log_file=f"game_logs/trust_game_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
            log_level=logging.DEBUG,
        )

        asyncio.run(
            main_trust_game_with_person_agent(
                logger=get_logger(),
                num_pairs=2,
                num_games=5,
                num_rounds=10,
                initial_funds=10,
                multiplication_factor=3.0,
            )
        )
    elif choice == "5":
        setup_logging(
            log_file=f"game_logs/volunteer_dilemma_with_person_agent-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
            log_level=logging.DEBUG,
        )

        asyncio.run(
            main_volunteer_dilemma_with_person_agent(
                logger=get_logger(),
                num_agents=4,
                num_games=5,
                num_rounds=10,
                benefit_b=100,
                cost_c=40,
            )
        )
    elif choice == "6":
        # 執行所有遊戲
        asyncio.run(run_all_games())
    else:
        print("無效的選擇，程式退出。")
        exit(1)
