# ruff: noqa: E402,F841

import asyncio
import json
import logging
import os
import pickle
import numpy as np
from datetime import datetime
from dotenv import load_dotenv

# load_dotenv(".env.openrouter")
load_dotenv()

from agentsociety2.contrib.env.mobility_space import MobilitySpace
from agentsociety2.contrib.env.event_space import EventSpace
from agentsociety2.contrib.env.simple_social_space import SimpleSocialSpace
from agentsociety2.contrib.env.social_media import SocialMediaSpace
from agentsociety2.agent import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.society import AgentSociety
from agentsociety2.logger import setup_logging, get_logger


def _setup_debugpy_if_enabled(logger) -> None:
    """可選啟用 debugpy attach（預設關閉）。"""
    enabled = os.getenv("ENABLE_DEBUGGER", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return

    host = os.getenv("DEBUGPY_HOST", "localhost")
    port = int(os.getenv("DEBUGPY_PORT", "5678"))

    try:
        import debugpy

        debugpy.listen((host, port))
        logger.info("debugpy enabled, waiting for debugger attach at %s:%s", host, port)
        debugpy.wait_for_client()
        logger.info("debugger attached, continuing simulation startup")
    except Exception as e:
        logger.exception("failed to initialize debugpy: %s", e)
        raise


def _calculate_gyration_radius(trajectories: list) -> float:
    """
    計算迴旋半徑（Radius of Gyration）
    
    迴旋半徑是從軌跡質心到各個位置點的平均距離的均方根。
    
    Args:
        trajectories: 軌跡列表，每個元素是 (x, y) 座標對
    
    Returns:
        迴旋半徑（單位：米）
    """
    if len(trajectories) == 0:
        return 0.0
    
    trajectories = np.array(trajectories)
    # 計算軌跡的質心
    centroid = trajectories.mean(axis=0)
    
    # 計算每個點到質心的距離
    distances = np.linalg.norm(trajectories - centroid, axis=1)
    
    # 計算均方根距離（迴旋半徑）
    gyration_radius = np.sqrt(np.mean(distances ** 2))
    
    return float(gyration_radius)


async def main(
    logger,
    num_agents: int = 50,
    profile_start_idx: int = 0,
):
    """
    執行整合多個環境模組的 Benchmark

    實驗設定：
    - 模擬起點：當日早上 00:00:00 (UTC)
    - 時間步長：15 分鐘 = 900 秒
    - 總步數：97 步（覆蓋 24+ 小時）
    
    環境模組：
    1. 移動模組（MobilitySpace）：管理 agent 的地理位置和軌跡
    2. 事件模組（EventSpace）：處理環境中的事件
    3. 社交媒體模組（SocialMediaSpace）：處理社交互動和媒體內容
    
    資料統計：
    - 軌跡資料：每個agent的移動軌跡（(x, y) 座標列表）
    - 訪問的AOI：每個agent訪問過的AOI集合
    - 迴旋半徑：衡量agent活動範圍的指標
    - 日均訪問地點數：每個agent訪問的唯一地點數
    """
    logger.info("\n" + "=" * 80)
    logger.info("【整合多模組 Benchmark】")
    logger.info("=" * 80)
    logger.info("實驗設定：")
    logger.info("  - 起始時間: 當日早上 00:00:00 (UTC)")
    logger.info("  - 時間步長: 15 分鐘 (900 秒)")
    logger.info("  - 總步數: 97 步")
    logger.info(f"  - Agent 數量: {num_agents}")
    logger.info("【環境模組】:")
    logger.info("  1. 移動模組 (MobilitySpace)")
    logger.info("  2. 事件模組 (EventSpace)")
    logger.info("  3. 社交媒體模組 (SocialMediaSpace)")
    logger.info("=" * 80)

    # 實驗引數
    # 從早上 7 點開始模擬
    START_TIME = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    TIME_STEP_MINUTES = 15  # 15 分鐘
    TIME_STEP_SECONDS = TIME_STEP_MINUTES * 60  # 900 秒
    TOTAL_STEPS = 97

    # 用於儲存需要清理的環境
    mobility_env = None
    event_space = None
    social_media_env = None
    env_router = None
    agents = []

    # ==================== 載入 Profiles ====================
    logger.info("\n【步驟1/4】載入 profiles.json...")

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

    # 【關鍵修復】動態獲取實際的 agent_ids，而不是硬編碼 1-num_agents
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")

    # ==================== 初始化環境 ====================
    logger.info("\n【步驟2/4】初始化環境...")

    # ==================== 建立 Agents ====================
    logger.info(f"\n【步驟3/4】建立 {num_agents} 個 Agents...")

    agent_args = []
    mobility_persons = []
    for profile in profiles_to_use:
        agent_id = profile["id"]

        # 建立 agent（使用 profile 中的詳細資訊）
        # 構建個人資料字串
        profile_text = f"My name is Agent-{agent_id}, age {profile.get('age', 30)}, gender {profile.get('gender', 'Unknown')}, education {profile.get('education', 'Unknown')}, occupation {profile.get('occupation', 'Unknown')}, home at {profile['home']}, work at {profile['work']}"

        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,
                "template_mode_enabled": True,
                "ask_intention_enabled": True,
            }
        )
        mobility_persons.append(
            {
                "id": agent_id,
                "position": {
                    "kind": "aoi",
                    "aoi_id": profile["home"],
                },
            }
        )

    # 建立 MobilitySpace 環境
    # 使用相對路徑而不是硬編碼的 /root 路徑
    home_dir = os.path.join(os.path.expanduser("~"), "agentsociety_data")
    map_path = os.path.join(home_dir, "beijing.pb")
    os.makedirs(home_dir, exist_ok=True)

    mobility_env = MobilitySpace(map_path, home_dir, persons=mobility_persons)
    # person = await mobility_env.get_person(1)
    # print(person)
    # input("Press Enter to continue...")
    event_space = EventSpace()
    
    # 建立社交媒體環境
    logger.info("\n【初始化社交媒體模組】")
    social_media_data_dir = os.getenv(
        "SOCIAL_MEDIA_DATA_DIR",
        os.path.join(os.path.expanduser("~/.agentsociety"), "social_media_data")
    )
    logger.info(f"  ✓ 社交媒體資料目錄: {social_media_data_dir}")
    social_media_env = SocialMediaSpace(data_dir=social_media_data_dir)

    # 建立 CodeGenRouter
    env_router = CodeGenRouter(
        env_modules=[mobility_env, event_space, social_media_env],
        log_path=f"logs/instruction_log_{datetime.now().strftime('%Y%m%d%H%M%S')}.pkl",
    )

    # 儲存 pyi 程式碼
    with open("tools_pyi.pyi", "w") as f:
        f.write(env_router._tools_pyi_dict[(False, None)])

    # 生成世界描述（使用快取）
    world_description = await env_router.get_world_description()
    print("--------------------------------")
    print(world_description)
    print("--------------------------------")

    # 實際初始化agents
    agents = [PersonAgent(**args) for args in agent_args]

    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=START_TIME,
    )
    await society.init()

    await society.run(num_steps=TOTAL_STEPS, tick=TIME_STEP_SECONDS)

    # ==================== 提取移動相關資料 ====================
    logger.info("\n【步驟5/5】提取移動統計資料...")
    
    # 從 MobilitySpace 環境中獲取移動相關資料
    trajectories_dict = mobility_env.get_all_persons_trajectories()
    visited_aois_dict = mobility_env.get_all_persons_visited_aois()
    
    # 計算各項指標
    gyration_radius_list = []
    daily_location_numbers_list = []
    trajectory_lengths = []
    
    for agent_id in actual_agent_ids:
        # 獲取該agent的軌跡
        trajectory = trajectories_dict.get(agent_id, [])
        visited_aois = visited_aois_dict.get(agent_id, set())
        
        # 計算迴旋半徑
        gr = _calculate_gyration_radius(trajectory)
        gyration_radius_list.append(gr)
        
        # 計算訪問的唯一AOI數量
        dln = len(visited_aois)
        daily_location_numbers_list.append(dln)
        
        # 記錄軌跡長度
        trajectory_lengths.append(len(trajectory))
        
        logger.info(f"  Agent {agent_id}:")
        logger.info(f"    - 軌跡點數: {len(trajectory)}")
        logger.info(f"    - 訪問AOI數: {dln}")
        logger.info(f"    - 迴旋半徑: {gr:.2f} 米")
        if len(visited_aois) > 0:
            logger.info(f"    - 訪問的AOI ID: {sorted(visited_aois)[:5]}{'...' if len(visited_aois) > 5 else ''}")
    
    # 轉換為 numpy 陣列
    results = {
        "gyration_radius": np.array(gyration_radius_list, dtype=np.float64),
        "daily_location_numbers": np.array(daily_location_numbers_list, dtype=np.int32),
        "trajectories": trajectories_dict,  # 保留原始軌跡資料
        "visited_aois": visited_aois_dict,  # 保留訪問的AOI資料
    }
    
    logger.info("\n  ✓ 資料提取完成")
    logger.info(f"    - gyration_radius shape: {results['gyration_radius'].shape}")
    logger.info(f"    - gyration_radius mean: {results['gyration_radius'].mean():.2f} 米")
    logger.info(f"    - gyration_radius std: {results['gyration_radius'].std():.2f} 米")
    logger.info(f"    - daily_location_numbers shape: {results['daily_location_numbers'].shape}")
    logger.info(f"    - daily_location_numbers mean: {results['daily_location_numbers'].mean():.2f}")
    logger.info(f"    - daily_location_numbers max: {results['daily_location_numbers'].max()}")
    
    # ==================== 儲存結果 ====================
    logger.info("\n【儲存結果】")
    
    output_dir = "benchmark_results"
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    result_file = os.path.join(output_dir, f"daily_mobility_results_{timestamp}.pkl")
    
    # 準備儲存的資料
    save_data = {
        "results": {
            "gyration_radius": results["gyration_radius"],
            "daily_location_numbers": results["daily_location_numbers"],
        },
        "trajectories": results["trajectories"],
        "visited_aois": results["visited_aois"],
        "metadata": {
            "num_agents": num_agents,
            "actual_agent_ids": actual_agent_ids,
            "total_steps": TOTAL_STEPS,
            "time_step_minutes": TIME_STEP_MINUTES,
            "start_time": START_TIME.isoformat(),
            "timestamp": timestamp,
        }
    }
    
    with open(result_file, "wb") as f:
        pickle.dump(save_data, f)
    
    logger.info(f"  ✓ 結果已儲存到: {result_file}")
    
    # 同時儲存為JSON格式以便檢視
    json_file = os.path.join(output_dir, f"daily_mobility_results_{timestamp}.json")
    json_data = {
        "results": {
            "gyration_radius": results["gyration_radius"].tolist(),
            "daily_location_numbers": results["daily_location_numbers"].tolist(),
        },
        "metadata": save_data["metadata"]
    }
    with open(json_file, "w") as f:
        json.dump(json_data, f, indent=2)
    
    logger.info(f"  ✓ JSON格式結果已儲存到: {json_file}")

    await society.close()

async def main_social(
    logger,
    num_agents: int = 1,
    profile_start_idx: int = 0,
):
    """
    執行 DailyMobility Benchmark

    實驗設定：
    - 模擬起點：當日早上 00:00:00 (UTC)
    - 時間步長：15 分鐘 = 900 秒
    - 總步數：97 步（覆蓋 24+ 小時）
    """
    logger.info("\n" + "=" * 80)
    logger.info("【DailyMobility Benchmark】")
    logger.info("=" * 80)
    logger.info("實驗設定：")
    logger.info("  - 起始時間: 當日早上 00:00:00 (UTC)")
    logger.info("  - 時間步長: 15 分鐘 (900 秒)")
    logger.info("  - 總步數: 97 步 (覆蓋 7:00 - 23:15)")
    logger.info(f"  - Agent 數量: {num_agents}")
    logger.info("=" * 80)

    # 實驗引數
    # 從早上 7 點開始模擬
    START_TIME = datetime.now().replace(hour=7, minute=0, second=0, microsecond=0)
    TIME_STEP_MINUTES = 15  # 15 分鐘
    TIME_STEP_SECONDS = TIME_STEP_MINUTES * 60  # 900 秒
    TOTAL_STEPS = 97

    # 用於儲存需要清理的環境
    mobility_env = None
    env_router = None
    agents = []

    # ==================== 載入 Profiles ====================
    logger.info("\n【步驟1/4】載入 profiles.json...")

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

    # 【關鍵修復】動態獲取實際的 agent_ids，而不是硬編碼 1-num_agents
    actual_agent_ids = [p["id"] for p in profiles_to_use]
    logger.info(f"  ✓ 實際 Agent IDs: {actual_agent_ids}")

    # ==================== 初始化環境 ====================
    logger.info("\n【步驟2/4】初始化環境...")

    # ==================== 建立 Agents ====================
    logger.info(f"\n【步驟3/4】建立 {num_agents} 個 Agents...")

    agent_args = []
    mobility_persons = []
    for profile in profiles_to_use:
        agent_id = profile["id"]

        # 建立 agent（使用 profile 中的詳細資訊）
        # 構建個人資料字串
        profile_text = f"My name is Agent-{agent_id}, age {profile.get('age', 30)}, gender {profile.get('gender', 'Unknown')}, education {profile.get('education', 'Unknown')}, occupation {profile.get('occupation', 'Unknown')}, home at {profile['home']}, work at {profile['work']}"

        agent_args.append(
            {
                "id": agent_id,
                "profile": profile_text,
            }
        )
        mobility_persons.append(
            {
                "id": agent_id,
                "position": {
                    "kind": "aoi",
                    "aoi_id": profile["home"],
                },
            }
        )

    # 建立 MobilitySpace 環境
    # 使用相對路徑而不是硬編碼的 /root 路徑
    home_dir = os.path.join(os.path.expanduser("~"), "agentsociety_data")
    map_path = os.path.join(home_dir, "beijing.pb")
    os.makedirs(home_dir, exist_ok=True)

    social_env = SimpleSocialSpace(
        agent_id_name_pairs=[
            (agent_id, profile.get("name", f"Agent-{agent_id}"))
            for agent_id, profile in zip(actual_agent_ids, profiles_to_use)
        ]
    )
    # # 建立 DailySpace 環境（使用實際的 agent_ids）
    # daily_env = DailySpace(person_ids=actual_agent_ids)

    # 建立 CodeGenRouter
    env_router = CodeGenRouter(env_modules=[social_env])

    # 生成世界描述（使用快取）
    world_description = await env_router.get_world_description()
    print("--------------------------------")
    print(world_description)
    print("--------------------------------")

    # 實際初始化agents
    agents = [PersonAgent(**args) for args in agent_args]

    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=START_TIME,
    )
    await society.init()

    await society.run(num_steps=TOTAL_STEPS, tick=TIME_STEP_SECONDS)

    await society.close()


if __name__ == "__main__":
    setup_logging(
        log_file=f"logs/daily_mobility_benchmark-{datetime.now().strftime('%Y%m%d%H%M%S')}.log",
        log_level=logging.DEBUG,
    )
    logger = get_logger()
    _setup_debugpy_if_enabled(logger)
    asyncio.run(main(logger=logger, num_agents=50))
