"""
声誉博弈实验主程序示例
演示如何使用 ReputationGameEnv（优化版本）和 LLMDonorAgent

本程序使用优化后的 reputation_game 模块，支持：
- Pydantic 模型验证
- 枚举类型增强类型安全
- 完整的统计功能（全局统计、声誉分布、智能体历史等）
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
    """运行声誉博弈实验"""

    # ========================================================================
    # 0. 设置日志
    # ========================================================================
    setup_logging(log_dir="log")
    logger = get_logger()

    # ========================================================================
    # 1. 环境配置
    # ========================================================================
    env_config = ReputationGameConfig(
        Z=10,  # 种群大小
        BENEFIT=5,  # 合作收益
        COST=1,  # 合作成本
        norm_type="stern_judging",  # 社会规范：可选 "image_score", "simple_standing", "stern_judging"
        seed=42,  # 随机种子
    )

    print("=" * 80)
    print("声誉博弈实验配置")
    print("=" * 80)
    print(f"种群大小 (Z): {env_config.Z}")
    print(f"合作收益 (BENEFIT): {env_config.BENEFIT}")
    print(f"合作成本 (COST): {env_config.COST}")
    print(f"社会规范 (norm_type): {env_config.norm_type}")
    print(f"随机种子 (seed): {env_config.seed}")
    print("=" * 80)

    # ========================================================================
    # 2. 仿真时间配置
    # ========================================================================
    TICK_DURATION = 2  # 每个tick的持续时间（秒）
    SIMULATION_DURATION = 30  # 仿真运行时长（秒）

    start_t = datetime.now()
    end_t = start_t + timedelta(seconds=SIMULATION_DURATION)


    # ========================================================================
    # 4. 创建环境
    # ========================================================================
    env_module = ReputationGameEnv(config=env_config)
    env_modules = cast(list[EnvBase], [env_module])
    
    # Create env_router
    env_router = CodeGenRouter(env_modules=env_modules)

    # ========================================================================
    # 5. 创建记忆系统配置（mem0）
    # ========================================================================
    # 创建 memory_config 字典，每个 agent 会使用此配置创建自己的 memory 实例
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
    # 6. 创建 Agent
    # ========================================================================
    # 定义个性列表（测试代码中完全控制）
    # 可以根据实验需求自定义个性，或者使用预定义的列表
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
        # 在测试代码中控制个性的分配方式
        # 方式1：随机选择（当前方式）
        personality = random.choice(personality_list)
        
        # 方式2：也可以直接指定个性字符串
        # personality = "You are a rational and cautious agent..."
        
        # 方式3：也可以根据 agent ID 分配特定个性
        # personality = personality_list[i % len(personality_list)]
        
        profile = {
            "id": f"agent-{i}",
            "name": f"Agent {i}",
            "custom_fields": {
                "learning_frequency": 5,  # 每5步学习一次
                "personality": personality,  # 明确传入个性（测试代码完全控制）
                # 可选：也可以传入其他参数
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
    print(f"\n创建了 {env_config.Z} 个 LLMDonorAgent 实例（ID 0-{env_config.Z-1}）\n")

    # ========================================================================
    # 7. 创建 Society 并运行
    # ========================================================================
    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=start_t,
    )

    try:
        await society.init()
        print("开始运行仿真...\n")
        expected_ticks = int(SIMULATION_DURATION / TICK_DURATION)
        print(f"模拟开始时间: {start_t.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"模拟结束时间: {end_t.strftime('%Y-%m-%d %H:%M:%S')}")
        print(
            f"预计运行时长: {SIMULATION_DURATION} 秒 ({SIMULATION_DURATION/60:.1f} 分钟)"
        )
        print(
            f"预计执行轮数: {expected_ticks} 轮 (每轮 {TICK_DURATION} 秒)\n"
        )

        # Run simulation until end_t
        # 添加进度监控（在后台任务中）
        progress_interval = 10  # 每10秒显示一次进度

        async def progress_monitor():
            """后台任务：定期显示进度"""
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
                    f"[进度] 已运行: {elapsed:.1f}秒 / {SIMULATION_DURATION}秒 ({progress:.1f}%), "
                    f"已执行: {current_ticks} 轮 / {expected_ticks} 轮, "
                    f"剩余: {remaining:.1f}秒, 当前模拟时间: {society.current_time.strftime('%H:%M:%S')}"
                )

        # 启动进度监控任务
        progress_task = asyncio.create_task(progress_monitor())

        try:
            # Run simulation for specified number of steps
            num_steps = int(SIMULATION_DURATION / TICK_DURATION)
            await society.run(num_steps=num_steps, tick=TICK_DURATION)
        finally:
            # 停止进度监控
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass

        print(f"\n模拟结束时间: {society.current_time.strftime('%Y-%m-%d %H:%M:%S')}")
        actual_duration = (society.current_time - start_t).total_seconds()
        # 计算实际执行的轮数（ticks）
        actual_ticks = int(actual_duration / TICK_DURATION)
        expected_ticks = int(SIMULATION_DURATION / TICK_DURATION)
        print(f"实际运行时长: {actual_duration:.1f} 秒")
        print(f"执行轮数: {actual_ticks} 轮 (预计: {expected_ticks} 轮, 每轮 {TICK_DURATION} 秒)")

        # 记录到日志
        logger.info("=" * 80)
        logger.info("实验正常结束")
        logger.info(
            f"模拟结束时间: {society.current_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        logger.info(f"实际运行时长: {actual_duration:.1f} 秒")
        logger.info(f"执行轮数: {actual_ticks} 轮 (预计: {expected_ticks} 轮)")
        logger.info("=" * 80)

        # 实验结束后，查询统计信息
        print("\n" + "=" * 80)
        print("实验结束，查询最终统计")
        print("=" * 80)

        # 1. 查询综合统计数据
        print("\n【综合统计数据】")
        stats_ctx, stats_ans = await env_module.get_global_statistics()
        print(stats_ans)
        
        # 计算每轮平均交互次数（如果有交互记录）
        total_interactions = stats_ctx.get('total_interactions', 0)
        avg_interactions_per_tick = total_interactions / actual_ticks if actual_ticks > 0 else 0
        print(f"\n每轮平均交互次数: {avg_interactions_per_tick:.2f} 次/轮")
        
        logger.info("实验统计数据:")
        logger.info(f"总互动次数: {total_interactions}")
        logger.info(f"合作次数: {stats_ctx.get('cooperation_count', 0)}")
        logger.info(f"背叛次数: {stats_ctx.get('defection_count', 0)}")
        logger.info(f"合作率 (η): {stats_ctx.get('cooperation_rate', 0):.4f}")
        logger.info(f"每轮平均交互次数: {avg_interactions_per_tick:.2f} 次/轮")

        # 2. 查询声誉分布
        print("\n【声誉分布统计】")
        rep_dist_ctx, rep_dist_ans = await env_module.get_reputation_distribution()
        print(rep_dist_ans)
        logger.info("声誉分布:")
        logger.info(f"好声誉数量: {rep_dist_ctx.get('good_count', 0)}")
        logger.info(f"坏声誉数量: {rep_dist_ctx.get('bad_count', 0)}")
        logger.info(f"好声誉比例: {rep_dist_ctx.get('good_ratio', 0):.4f}")

        # 3. 查询策略收敛性分析
        print("\n【策略收敛性分析】")
        convergence_ctx, convergence_ans = (
            await env_module.get_strategy_convergence_analysis(num_periods=3)
        )
        print(convergence_ans)
        logger.info("策略收敛性分析:")
        logger.info(f"趋势: {convergence_ctx.get('trend', 'unknown')}")
        logger.info(f"收敛状态: {convergence_ctx.get('convergence_status', 'unknown')}")
        logger.info(convergence_ctx.get("convergence_analysis", "N/A"))

        # 4. 查询各个智能体的收益和声誉
        print("\n【各智能体收益统计】")
        payoffs_info = []
        for agent_id in range(env_config.Z):
            payoff_ctx, payoff_ans = await env_module.get_agent_payoff(agent_id)
            rep_ctx, rep_ans = await env_module.get_agent_reputation(agent_id)
            payoffs_info.append({
                "agent_id": agent_id,
                "payoff": payoff_ctx.get("payoff", 0.0),
                "reputation": rep_ctx.get("reputation", "unknown"),
            })
        
        # 计算平均收益
        avg_payoff = sum(p["payoff"] for p in payoffs_info) / len(payoffs_info) if payoffs_info else 0.0
        
        print(f"平均收益: {avg_payoff:.2f}")
        for info in sorted(payoffs_info, key=lambda x: x["payoff"], reverse=True):
            print(f"  Agent {info['agent_id']}: 收益={info['payoff']:.2f}, 声誉={info['reputation']}")
        
        logger.info(f"平均收益: {avg_payoff:.2f}")

        # 5. 查询顶尖智能体
        print("\n【顶尖智能体排名】")
        top_ctx, top_ans = await env_module.get_top_agent_summary(top_k=5)
        print(top_ans)

        # 6. 查询公共日志（可选，用于详细分析）
        print("\n【最近交互记录】")
        log_ctx, log_ans = await env_module.get_public_action_log(limit=10)
        print(log_ans)

        # 可选：查询某个智能体的详细历史（示例：查询 Agent 0）
        if env_config.Z > 0:
            print("\n【智能体历史示例（Agent 0）】")
            history_ctx, history_ans = await env_module.get_agent_history(agent_id=0, limit=10)
            print(history_ans)
            logger.info(f"Agent 0 最近 {len(history_ctx.get('history', []))} 次互动记录")

        # 7. 保存统计数据到文件（可选）
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
            "recent_logs": log_ctx.get("log", [])[:10],  # 保存最近10条日志
        }

        with open(stats_file, "w", encoding="utf-8") as f:
            json.dump(statistics_data, f, indent=2, ensure_ascii=False)

        print(f"\n统计数据已保存到: {stats_file}")
        logger.info(f"统计数据已保存到: {stats_file}")

    except KeyboardInterrupt:
        logger.info("实验被用户中断 (Ctrl+C)")
        print("\n实验被用户中断")
        raise
    except Exception as e:
        logger.error(f"实验运行出错: {e}", exc_info=True)
        print(f"\n实验运行出错: {e}")
        raise
    finally:
        logger.info("开始关闭 Society...")
        print("\n关闭 Society...")
        await society.close()
        logger.info("Society 已关闭")
        print("Society 已关闭")


if __name__ == "__main__":
    asyncio.run(main())
