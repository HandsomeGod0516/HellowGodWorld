"""
聲譽博弈實驗主程式示例
演示如何使用 ReputationGameEnv（最佳化版本）和 LLMDonorAgent

本程式使用最佳化後的 reputation_game 模組，支援：
- Pydantic 模型驗證
- 列舉型別增強型別安全
- 完整的統計功能（全域性統計、聲譽分佈、智慧體歷史等）
"""

import asyncio
import logging
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from agentsociety2.agent.base import AgentBase
from agentsociety2.contrib.env.reputation_game import (
    ReputationGameEnv,
    ReputationGameConfig,
)
from agentsociety2.contrib.agent.llm_donor_agent import LLMDonorAgent
from agentsociety2.env import EnvBase, CodeGenRouter
from agentsociety2.society.society import AgentSociety
from agentsociety2.logger import get_logger
from dotenv import load_dotenv
from mem0.configs.base import VectorStoreConfig
from mem0.embeddings.configs import EmbedderConfig
from mem0.llms.configs import LlmConfig
from mem0.memory.main import MemoryConfig

load_dotenv()


def setup_logging(log_dir: str = "log"):
    """
    Setup logging to both console and file.

    Args:
        log_dir: Directory to save log files (default: "log")
    """
    # Create log directory if it doesn't exist
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Generate log filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_path / f"reputation_game_{timestamp}.log"

    # Get the logger
    logger = get_logger()

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Set log level
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Console handler (stdout)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.info(f"Logging initialized. Log file: {log_file}")
    print(f"📝 Log file: {log_file}")

    return log_file


async def main():
    """執行聲譽博弈實驗"""

    # ========================================================================
    # 0. 設定日誌
    # ========================================================================
    setup_logging(log_dir="log")
    logger = get_logger()

    # ========================================================================
    # 1. 環境配置
    # ========================================================================
    env_config = ReputationGameConfig(
        Z=10,  # 種群大小
        BENEFIT=5,  # 合作收益
        COST=1,  # 合作成本
        norm_type="stern_judging",  # 社會規範：可選 "image_score", "simple_standing", "stern_judging"
        seed=42,  # 隨機種子
    )

    print("=" * 80)
    print("聲譽博弈實驗配置")
    print("=" * 80)
    print(f"種群大小 (Z): {env_config.Z}")
    print(f"合作收益 (BENEFIT): {env_config.BENEFIT}")
    print(f"合作成本 (COST): {env_config.COST}")
    print(f"社會規範 (norm_type): {env_config.norm_type}")
    print(f"隨機種子 (seed): {env_config.seed}")
    print("=" * 80)

    # ========================================================================
    # 2. 模擬時間配置
    # ========================================================================
    TICK_DURATION = 2  # 每個tick的持續時間（秒）
    SIMULATION_DURATION = 30  # 模擬執行時長（秒）

    start_t = datetime.now()
    end_t = start_t + timedelta(seconds=SIMULATION_DURATION)


    # ========================================================================
    # 4. 建立環境
    # ========================================================================
    env_module = ReputationGameEnv(config=env_config)
    env_modules = cast(list[EnvBase], [env_module])
    
    # Create env_router
    env_router = CodeGenRouter(env_modules=env_modules)

    # ========================================================================
    # 5. 建立記憶系統配置（mem0）
    # ========================================================================
    # 建立 memory_config 字典，每個 agent 會使用此配置建立自己的 memory 例項
    memory_config = MemoryConfig(
        vector_store=VectorStoreConfig(
            config={
                "embedding_model_dims": 1024,
            },
        ),
        llm=LlmConfig(
            provider="openai",
            config={
                "model": "qwen2.5-7b-instruct",
                "api_key": os.getenv("API_KEY"),
                "openai_base_url": "https://cloud.infini-ai.com/maas/v1",
            },
        ),
        embedder=EmbedderConfig(
            provider="openai",
            config={
                "model": "bge-m3",
                "api_key": os.getenv("API_KEY"),
                "openai_base_url": "https://cloud.infini-ai.com/maas/v1",
                "embedding_dims": 1024,
            },
        ),
    ).model_dump()

    # ========================================================================
    # 6. 建立 Agent
    # ========================================================================
    # 定義個性列表（測試程式碼中完全控制）
    # 可以根據實驗需求自定義個性，或者使用預定義的列表
    personality_list = [
        "You are a rational and cautious agent, tending to maximize long-term benefits.",
        "You are an emotional agent, and your decisions are influenced by your current emotional state.",
        "You are a fair-minded agent, tending to help those with good reputation and refusing to help those with bad reputation.",
        "You are an altruistic agent, more willing to help others even if it may harm your short-term benefits.",
        "You are a selfish agent, mainly focusing on your own benefits and not caring much about others' reputation.",
        "You are a vengeful agent, if others treat you badly, you will remember and take revenge.",
        "You are an optimistic agent, believing that cooperation will bring better results.",
        "You are a pessimistic agent, tending to protect yourself and not trusting others much.",
    ]
    
    agents_list: list[AgentBase] = []
    for i in range(env_config.Z):
        # 在測試程式碼中控制個性的分配方式
        # 方式1：隨機選擇（當前方式）
        personality = random.choice(personality_list)
        
        # 方式2：也可以直接指定個性字串
        # personality = "You are a rational and cautious agent..."
        
        # 方式3：也可以根據 agent ID 分配特定個性
        # personality = personality_list[i % len(personality_list)]
        
        profile = {
            "id": f"agent-{i}",
            "name": f"Agent {i}",
            "custom_fields": {
                "learning_frequency": 5,  # 每5步學習一次
                "personality": personality,  # 明確傳入個性（測試程式碼完全控制）
                # 可選：也可以傳入其他引數
                # "initial_mood": random.uniform(-1.0, 1.0),
                # "risk_tolerance": random.uniform(0.0, 1.0),
            },
        }
        agents_list.append(
            LLMDonorAgent(
                id=i,
                profile=profile,
                memory_config=memory_config,
            )
        )
    agents = cast(list[AgentBase], agents_list)
    print(f"\n建立了 {env_config.Z} 個 LLMDonorAgent 例項（ID 0-{env_config.Z-1}）\n")

    # ========================================================================
    # 7. 建立 Society 並執行
    # ========================================================================
    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=start_t,
    )

    try:
        await society.init()
        print("開始執行模擬...\n")
        expected_ticks = int(SIMULATION_DURATION / TICK_DURATION)
        print(f"模擬開始時間: {start_t.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"模擬結束時間: {end_t.strftime('%Y-%m-%d %H:%M:%S')}")
        print(
            f"預計執行時長: {SIMULATION_DURATION} 秒 ({SIMULATION_DURATION/60:.1f} 分鐘)"
        )
        print(
            f"預計執行輪數: {expected_ticks} 輪 (每輪 {TICK_DURATION} 秒)\n"
        )

        # Run simulation until end_t
        # 新增進度監控（在後臺任務中）
        progress_interval = 10  # 每10秒顯示一次進度

        async def progress_monitor():
            """後臺任務：定期顯示進度"""
            while True:
                await asyncio.sleep(progress_interval)
                if society.current_time >= end_t:
                    break
                elapsed = (society.current_time - start_t).total_seconds()
                remaining = (end_t - society.current_time).total_seconds()
                progress = (elapsed / SIMULATION_DURATION) * 100
                current_ticks = int(elapsed / TICK_DURATION)
                expected_ticks = int(SIMULATION_DURATION / TICK_DURATION)
                print(
                    f"[進度] 已執行: {elapsed:.1f}秒 / {SIMULATION_DURATION}秒 ({progress:.1f}%), "
                    f"已執行: {current_ticks} 輪 / {expected_ticks} 輪, "
                    f"剩餘: {remaining:.1f}秒, 當前模擬時間: {society.current_time.strftime('%H:%M:%S')}"
                )

        # 啟動進度監控任務
        progress_task = asyncio.create_task(progress_monitor())

        try:
            # Run simulation for specified number of steps
            num_steps = int(SIMULATION_DURATION / TICK_DURATION)
            await society.run(num_steps=num_steps, tick=TICK_DURATION)
        finally:
            # 停止進度監控
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        print(f"\n模擬結束時間: {society.current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        actual_duration = (society.current_time - start_t).total_seconds()
        # 計算實際執行的輪數（ticks）
        actual_ticks = int(actual_duration / TICK_DURATION)
        expected_ticks = int(SIMULATION_DURATION / TICK_DURATION)
        print(f"實際執行時長: {actual_duration:.1f} 秒")
        print(f"執行輪數: {actual_ticks} 輪 (預計: {expected_ticks} 輪, 每輪 {TICK_DURATION} 秒)")

        # 記錄到日誌
        logger.info("=" * 80)
        logger.info("實驗正常結束")
        logger.info(
            f"模擬結束時間: {society.current_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info(f"實際執行時長: {actual_duration:.1f} 秒")
        logger.info(f"執行輪數: {actual_ticks} 輪 (預計: {expected_ticks} 輪)")
        logger.info("=" * 80)

        # 實驗結束後，查詢統計資訊
        print("\n" + "=" * 80)
        print("實驗結束，查詢最終統計")
        print("=" * 80)

        # 1. 查詢綜合統計資料
        print("\n【綜合統計資料】")
        stats_ctx, stats_ans = await env_module.get_global_statistics()
        print(stats_ans)
        
        # 計算每輪平均互動次數（如果有互動記錄）
        total_interactions = stats_ctx.get('total_interactions', 0)
        avg_interactions_per_tick = total_interactions / actual_ticks if actual_ticks > 0 else 0
        print(f"\n每輪平均互動次數: {avg_interactions_per_tick:.2f} 次/輪")
        
        logger.info("實驗統計資料:")
        logger.info(f"總互動次數: {total_interactions}")
        logger.info(f"合作次數: {stats_ctx.get('cooperation_count', 0)}")
        logger.info(f"背叛次數: {stats_ctx.get('defection_count', 0)}")
        logger.info(f"合作率 (η): {stats_ctx.get('cooperation_rate', 0):.4f}")
        logger.info(f"每輪平均互動次數: {avg_interactions_per_tick:.2f} 次/輪")

        # 2. 查詢聲譽分佈
        print("\n【聲譽分佈統計】")
        rep_dist_ctx, rep_dist_ans = await env_module.get_reputation_distribution()
        print(rep_dist_ans)
        logger.info("聲譽分佈:")
        logger.info(f"好聲譽數量: {rep_dist_ctx.get('good_count', 0)}")
        logger.info(f"壞聲譽數量: {rep_dist_ctx.get('bad_count', 0)}")
        logger.info(f"好聲譽比例: {rep_dist_ctx.get('good_ratio', 0):.4f}")

        # 3. 查詢策略收斂性分析
        print("\n【策略收斂性分析】")
        convergence_ctx, convergence_ans = (
            await env_module.get_strategy_convergence_analysis(num_periods=3)
        )
        print(convergence_ans)
        logger.info("策略收斂性分析:")
        logger.info(f"趨勢: {convergence_ctx.get('trend', 'unknown')}")
        logger.info(f"收斂狀態: {convergence_ctx.get('convergence_status', 'unknown')}")
        logger.info(convergence_ctx.get("convergence_analysis", "N/A"))

        # 4. 查詢各個智慧體的收益和聲譽
        print("\n【各智慧體收益統計】")
        payoffs_info = []
        for agent_id in range(env_config.Z):
            payoff_ctx, payoff_ans = await env_module.get_agent_payoff(agent_id)
            rep_ctx, rep_ans = await env_module.get_agent_reputation(agent_id)
            payoffs_info.append({
                "agent_id": agent_id,
                "payoff": payoff_ctx.get("payoff", 0.0),
                "reputation": rep_ctx.get("reputation", "unknown"),
            })
        
        # 計算平均收益
        avg_payoff = sum(p["payoff"] for p in payoffs_info) / len(payoffs_info) if payoffs_info else 0.0
        
        print(f"平均收益: {avg_payoff:.2f}")
        for info in sorted(payoffs_info, key=lambda x: x["payoff"], reverse=True):
            print(f"  Agent {info['agent_id']}: 收益={info['payoff']:.2f}, 聲譽={info['reputation']}")
        
        logger.info(f"平均收益: {avg_payoff:.2f}")

        # 5. 查詢頂尖智慧體
        print("\n【頂尖智慧體排名】")
        top_ctx, top_ans = await env_module.get_top_agent_summary(top_k=5)
        print(top_ans)

        # 6. 查詢公共日誌（可選，用於詳細分析）
        print("\n【最近互動記錄】")
        log_ctx, log_ans = await env_module.get_public_action_log(limit=10)
        print(log_ans)

        # 可選：查詢某個智慧體的詳細歷史（示例：查詢 Agent 0）
        if env_config.Z > 0:
            print("\n【智慧體歷史示例（Agent 0）】")
            history_ctx, history_ans = await env_module.get_agent_history(agent_id=0, limit=10)
            print(history_ans)
            logger.info(f"Agent 0 最近 {len(history_ctx.get('history', []))} 次互動記錄")

        # 7. 儲存統計資料到檔案（可選）
        import json
        from pathlib import Path

        stats_file = (
            Path("log") / f"statistics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        stats_file.parent.mkdir(exist_ok=True)

        statistics_data = {
            "experiment_config": {
                "Z": env_config.Z,
                "BENEFIT": env_config.BENEFIT,
                "COST": env_config.COST,
                "norm_type": env_config.norm_type.value if hasattr(env_config.norm_type, 'value') else str(env_config.norm_type),
                "seed": env_config.seed,
            },
            "simulation_info": {
                "start_time": start_t.isoformat(),
                "end_time": society.current_time.isoformat(),
                "duration_seconds": actual_duration,
            },
            "statistics": {
                **stats_ctx,
                "average_payoff": avg_payoff,
            },
            "reputation_distribution": rep_dist_ctx,
            "convergence_analysis": convergence_ctx,
            "agent_payoffs": payoffs_info,
            "top_agents": top_ctx.get("top_agents", []),
            "recent_logs": log_ctx.get("log", [])[:10],  # 儲存最近10條日誌
        }

        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(statistics_data, f, indent=2, ensure_ascii=False)

        print(f"\n統計資料已儲存到: {stats_file}")
        logger.info(f"統計資料已儲存到: {stats_file}")

    except KeyboardInterrupt:
        logger.info("實驗被使用者中斷 (Ctrl+C)")
        print("\n實驗被使用者中斷")
        raise
    except Exception as e:
        logger.error(f"實驗執行出錯: {e}", exc_info=True)
        print(f"\n實驗執行出錯: {e}")
        raise
    finally:
        logger.info("開始關閉 Society...")
        print("\n關閉 Society...")
        await society.close()
        logger.info("Society 已關閉")
        print("Society 已關閉")


if __name__ == "__main__":
    asyncio.run(main())
