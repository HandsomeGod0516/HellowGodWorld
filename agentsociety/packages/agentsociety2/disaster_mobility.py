# ruff: noqa: E402

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

# 先載入環境變數，再強制關閉 mem0 telemetry，避免匯入 PersonAgent 時觸發埋點執行緒。
load_dotenv()
os.environ["MEM0_TELEMETRY"] = "False"

from agentsociety2.contrib.env.mobility_space import MobilitySpace
from agentsociety2.contrib.env.event_space import EventSpace
from agentsociety2.contrib.env.global_information import GlobalInformationEnv
from agentsociety2.agent import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.society import AgentSociety
from agentsociety2.logger import setup_logging, get_logger


async def main_disaster_mobility(
    logger,
    num_agents: int = 50,
    profile_start_idx: int = 0,
    profiles_path: str | None = None,
    map_path: str | None = None,
):
    """
    災害對出行影響實驗（11天，每小時一步）

    實驗設定：
    - Day 1: 正常日常移動
    - Day 3（當日一早）: 廣播突發山火
    - Day 4-Day 9: 每天廣播“山火還在持續”
    - Day 10（當日一早）: 廣播山火已被撲滅

    統計量：
    - 每天所有agent的出行量總和（move_to完成次數）
    """
    logger.info("\n" + "=" * 80)
    logger.info("【災害對出行影響實驗】")
    logger.info("=" * 80)

    # 時間設定：每小時一步，共11天
    start_time = datetime.now().replace(year=2026, month=2, day=9, hour=0, minute=0, second=0, microsecond=0)
    time_step_seconds = 60 * 60  # 1小時
    total_days = 11
    steps_per_day = 24
    total_steps = total_days * steps_per_day

    # ==================== 載入 Profiles ====================
    logger.info("\n【步驟1/4】載入 agent_profiles_ca_paradise.json...")
    if profiles_path is None:
        profiles_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../..", "agent_profiles_ca_paradise.json")
        )
    if not os.path.exists(profiles_path):
        logger.error(f"  ❌ agent profiles 檔案不存在: {profiles_path}")
        return

    with open(profiles_path, "r", encoding="utf-8") as f:
        profiles = json.load(f)

    logger.info(f"  ✓ 載入了 {len(profiles)} 個 agent profiles")

    if num_agents > len(profiles):
        logger.warning(
            f"  ⚠ 請求的 agent 數量 ({num_agents}) 超過 profiles 數量 ({len(profiles)})，使用全部 {len(profiles)} 個"
        )
        num_agents = len(profiles)

    profiles_to_use = profiles[profile_start_idx : profile_start_idx + num_agents]

    # ==================== 初始化環境 ====================
    logger.info("\n【步驟2/4】初始化環境...")
    import tempfile

    chroma_base_dir = tempfile.mkdtemp(prefix="chroma_memories_")
    logger.info(f"  ✓ 建立臨時chroma目錄: {chroma_base_dir}")

    # ==================== 建立 Agents ====================
    logger.info(f"\n【步驟3/4】建立 {num_agents} 個 Agents...")
    agent_args = []
    mobility_persons = []
    date_time_str = datetime.now().strftime("%Y%m%d%H%M%S")

    for idx, profile in enumerate(profiles_to_use, start=1):
        agent_str_id = profile.get("agent_id", f"agent_{idx:04d}")
        agent_id = idx  # 使用連續整數ID，方便MobilitySpace處理

        agent_chroma_path = os.path.join(
            chroma_base_dir, f"agent_{agent_id}_{date_time_str}"
        )
        os.makedirs(agent_chroma_path, exist_ok=True)
        agent_sqlite_path = os.path.join(chroma_base_dir, f"agent_{agent_id}.db")
        os.makedirs(os.path.dirname(agent_sqlite_path), exist_ok=True)

        profile_text = (
            f"My name is {agent_str_id}, "
            f"gender {profile.get('gender', 'Unknown')}, "
            f"race {profile.get('race', 'Unknown')}, "
            f"education {profile.get('education', 'Unknown')}, "
            f"transport_mode {profile.get('transport_mode', 'Unknown')}, "
            f"average_commuting_time {profile.get('average_commuting_time', 'Unknown')}, "
            f"median_income {profile.get('median_income', 'Unknown')}, "
            f"median_age {profile.get('median_age', 'Unknown')}, "
            f"average_household_size {profile.get('average_household_size', 'Unknown')}, "
            f"home at {profile.get('home_aoi_id')}, "
            f"work at {profile.get('work_aoi_id')}"
        )

        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,
                "ask_intention_enabled": False,
            }
        )

        mobility_persons.append(
            {
                "id": agent_id,
                "position": {
                    "kind": "aoi",
                    "aoi_id": int(profile["home_aoi_id"]),
                },
            }
        )

    # ==================== 建立環境與路由 ====================
    home_dir = os.path.join(os.path.expanduser("~"), "agentsociety_data")
    if map_path is None:
        map_path = os.path.join(home_dir, "map_us_ca_paradise.pb")
    os.makedirs(home_dir, exist_ok=True)

    mobility_env = MobilitySpace(map_path, home_dir, persons=mobility_persons)
    event_space = EventSpace()
    global_info_env = GlobalInformationEnv()

    env_router = CodeGenRouter(
        env_modules=[mobility_env, event_space, global_info_env],
        log_path=f"logs_disaster_ca/instruction_log_{datetime.now().strftime('%Y%m%d%H%M%S')}.pkl",
    )

    world_description = await env_router.generate_world_description_from_tools()
    print("--------------------------------")
    print(world_description)
    print("--------------------------------")

    agents = [PersonAgent(**args) for args in agent_args]
    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=start_time,
    )
    await society.init()

    # 廣播內容設定
    await global_info_env.set("今天一切正常")

    # ==================== 執行模擬並統計出行量 ====================
    daily_move_counts = [0 for _ in range(total_days)]

    for step_idx in range(total_steps):
        # Day 3 當日一早廣播“突發山火”
        if step_idx == 2 * steps_per_day:
            await global_info_env.set("緊急廣播：極端寒潮襲擊我市，請廣大民眾注意適當減少非必要出行")
        # Day 4 到 Day 9 每天開始時廣播“山火還在持續”
        elif step_idx % steps_per_day == 0 and 3 * steps_per_day <= step_idx < 9 * steps_per_day:
            await global_info_env.set("廣播：寒潮仍在持續，請廣大民眾注意適當減少非必要出行")
        # Day 10 當日一早廣播災害結束
        elif step_idx == 9 * steps_per_day:
            await global_info_env.set("廣播：寒潮已經結束，可恢復正常秩序")

        # 手動執行一步（複製 AgentSociety.step 的邏輯）
        society._t += timedelta(seconds=time_step_seconds)
        society._env_router.sync_simulation_clock(society._t)
        tasks = [agent.step(time_step_seconds, society._t) for agent in society._agents]
        await asyncio.gather(*tasks)

        # 在環境 step 前記錄當前移動中的人
        moving_before_env = {
            pid for pid, person in mobility_env._persons.items() if person.status == "moving"
        }

        await society._env_router.step(time_step_seconds, society._t)
        society._step_count += 1

        # 統計本步完成的出行（move_to完成）
        completed_moves = 0
        for pid in moving_before_env:
            person = mobility_env._persons.get(pid)
            if person is not None and person.status == "idle":
                completed_moves += 1

        day_index = step_idx // steps_per_day
        daily_move_counts[day_index] += completed_moves

        if (step_idx + 1) % steps_per_day == 0:
            logger.info(
                f"  ✓ Day {day_index + 1} 出行量總和: {daily_move_counts[day_index]}"
            )

    # ==================== 儲存統計結果 ====================
    output_dir = "benchmark_results"
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = os.path.join(
        output_dir, f"disaster_mobility_daily_moves_{timestamp}.json"
    )
    save_data = {
        "daily_move_counts": daily_move_counts,
        "metadata": {
            "num_agents": num_agents,
            "total_days": total_days,
            "steps_per_day": steps_per_day,
            "time_step_seconds": time_step_seconds,
            "start_time": start_time.isoformat(),
            "profiles_path": profiles_path,
            "map_path": map_path,
            "timestamp": timestamp,
        },
    }
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(save_data, f, indent=2, ensure_ascii=False)
    logger.info(f"  ✓ 統計結果已儲存到: {result_file}")

    await society.close()


if __name__ == "__main__":
    setup_logging(
        log_file=f"logs_disaster_ca/disaster_mobility-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
        log_level=logging.DEBUG,
    )
    asyncio.run(main_disaster_mobility(logger=get_logger()))
