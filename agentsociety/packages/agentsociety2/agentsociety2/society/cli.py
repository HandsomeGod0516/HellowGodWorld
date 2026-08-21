# ruff: noqa: E402

"""命令列介面，用於快速啟動AgentSociety2模擬實驗"""

import argparse
import asyncio
import json
import os
import sys
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

# 強制禁用遙測（在任何匯入之前）
# 禁用 mem0 遙測（避免連線 Posthog/Facebook）
os.environ["MEM0_TELEMETRY"] = "False"
# 禁用 ChromaDB 遙測（同樣使用 Posthog）
os.environ["ANONYMIZED_TELEMETRY"] = "False"


# 禁用 Posthog 客戶端建立（在匯入前）
def _disable_posthog_import():
    """在模組匯入前禁用 Posthog"""
    import sys

    # 建立一個假模組來阻止 posthog 匯入
    class FakePosthog:
        class Posthog:
            def __init__(self, *args, **kwargs):
                pass

            def capture(self, *args, **kwargs):
                pass

            def disable(self):
                pass

        def __getattr__(self, *args, **kwargs):
            return self.Posthog()

    # 將 posthog 新增到 sys.modules 以阻止其匯入
    sys.modules["posthog"] = FakePosthog()
    sys.modules["posthog.client"] = FakePosthog()
    sys.modules["posthog.capture"] = lambda *a, **k: None


_disable_posthog_import()

from agentsociety2.agent import AgentBase
from agentsociety2.env import CodeGenRouter, EnvBase
from agentsociety2.registry import (
    get_registered_env_modules,
    get_registered_agent_modules,
    scan_and_register_custom_modules,
)
from agentsociety2.storage import ReplayWriter
from agentsociety2.society.models import (
    InitConfig,
    RunStep,
    AskStep,
    InterveneStep,
    QuestionnaireStep,
    StepsConfig,
)
from agentsociety2.society.questionnaire import Questionnaire
from agentsociety2.society.society import AgentSociety
from agentsociety2.logger import get_logger, set_logger_level, add_file_handler

logger = get_logger()


def _validate_env_early() -> None:
    """早期環境變數驗證（在 main 入口處呼叫）"""
    errors = []

    # 檢查主要 LLM API key
    llm_api_key = os.getenv("AGENTSOCIETY_LLM_API_KEY", "")
    if not llm_api_key or not llm_api_key.strip():
        errors.append("AGENTSOCIETY_LLM_API_KEY")

    # 檢查 coder LLM（必須有，因為 CodeGenRouter 需要）
    coder_api_key = os.getenv("AGENTSOCIETY_CODER_LLM_API_KEY") or llm_api_key
    if not coder_api_key or not coder_api_key.strip():
        errors.append("AGENTSOCIETY_CODER_LLM_API_KEY (or AGENTSOCIETY_LLM_API_KEY)")

    # 檢查 nano LLM（用於 fallback）
    nano_api_key = os.getenv("AGENTSOCIETY_NANO_LLM_API_KEY") or llm_api_key
    if not nano_api_key or not nano_api_key.strip():
        errors.append("AGENTSOCIETY_NANO_LLM_API_KEY (or AGENTSOCIETY_LLM_API_KEY)")

    # 確認 mem0 遙測已禁用
    mem0_telemetry = os.getenv("MEM0_TELEMETRY", "False").lower()
    if mem0_telemetry not in ("false", "0", "no", ""):
        errors.append(f"MEM0_TELEMETRY must be 'False', currently: {mem0_telemetry}")

    # 確認 ChromaDB 遙測已禁用
    chroma_telemetry = os.getenv("ANONYMIZED_TELEMETRY", "False").lower()
    if chroma_telemetry not in ("false", "0", "no", ""):
        errors.append(
            f"ANONYMIZED_TELEMETRY must be 'False', currently: {chroma_telemetry}"
        )

    if errors:
        print("❌ Environment configuration error:", file=sys.stderr)
        for error in errors:
            print(f"  Missing: {error}", file=sys.stderr)
        print(
            "\nPlease configure these in your .env file before running experiments.",
            file=sys.stderr,
        )
        sys.exit(1)


class ExperimentRunner:
    """實驗執行器，負責載入配置、建立例項和執行步驟"""

    def __init__(self, run_dir: Path):
        """
        初始化實驗執行器

        :param run_dir: run/ 目錄路徑，作為實驗的 HOME 目錄。
        """
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # 建立必要的子目錄
        self.artifacts_dir = self.run_dir / "artifacts"
        self.artifacts_dir.mkdir(exist_ok=True)

        # 檔案路徑
        self.pid_file = self.run_dir / "pid.json"
        self.db_file = self.run_dir / "sqlite.db"

        self.society: Optional[AgentSociety] = None
        self._should_terminate = False

    def _validate_environment(self) -> None:
        """驗證所有必需的環境變數，缺漏則報錯退出"""
        errors = []

        # 檢查主要 LLM 配置
        llm_api_key = os.getenv("AGENTSOCIETY_LLM_API_KEY", "")
        if not llm_api_key or not llm_api_key.strip():
            errors.append(
                "Missing required environment variable: AGENTSOCIETY_LLM_API_KEY"
            )

        # 檢查 coder LLM 配置（CodeGenRouter 需要）
        coder_api_key = os.getenv("AGENTSOCIETY_CODER_LLM_API_KEY") or llm_api_key
        if not coder_api_key or not coder_api_key.strip():
            errors.append(
                "Missing required environment variable: AGENTSOCIETY_CODER_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
            )

        # 檢查 nano LLM 配置（用於 fallback）
        nano_api_key = os.getenv("AGENTSOCIETY_NANO_LLM_API_KEY") or llm_api_key
        if not nano_api_key or not nano_api_key.strip():
            errors.append(
                "Missing required environment variable: AGENTSOCIETY_NANO_LLM_API_KEY or AGENTSOCIETY_LLM_API_KEY"
            )

        # 檢查 mem0 遙測是否禁用
        mem0_telemetry = os.getenv("MEM0_TELEMETRY", "False").lower()
        if mem0_telemetry not in ("false", "0", "no", ""):
            errors.append(
                f"MEM0_TELEMETRY must be disabled (set to 'False'), currently: {mem0_telemetry}"
            )

        # 檢查 ChromaDB 遙測是否禁用
        chroma_telemetry = os.getenv("ANONYMIZED_TELEMETRY", "False").lower()
        if chroma_telemetry not in ("false", "0", "no", ""):
            errors.append(
                f"ANONYMIZED_TELEMETRY must be disabled (set to 'False'), currently: {chroma_telemetry}"
            )

        # 如果有錯誤，列印詳細資訊並退出
        if errors:
            logger.error("Environment validation failed:")
            for error in errors:
                logger.error(f"  - {error}")
            print(
                "\n❌ Environment validation failed. Required configuration:",
                file=sys.stderr,
            )
            for error in errors:
                print(f"  ❌ {error}", file=sys.stderr)
            print(
                "\nPlease set the required environment variables in your .env file.",
                file=sys.stderr,
            )
            sys.exit(1)

        logger.info("Environment validation passed")
        # 確認遙測已禁用
        logger.info(
            "Telemetry disabled: MEM0_TELEMETRY=False, ANONYMIZED_TELEMETRY=False"
        )

    def _load_config(self, config_path: Path) -> InitConfig:
        """載入並驗證配置檔案"""
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            if config_path.suffix.lower() == ".json":
                data = json.load(f)
            elif config_path.suffix.lower() in [".yaml", ".yml"]:
                data = yaml.safe_load(f)
            else:
                raise ValueError(
                    f"Unsupported config file format: {config_path.suffix}"
                )

        # 使用pydantic驗證配置
        try:
            return InitConfig.model_validate(data)
        except Exception as e:
            raise ValueError(f"Invalid config file format: {e}") from e

    def _load_steps(self, steps_path: Path) -> StepsConfig:
        """載入並驗證 steps.yaml"""
        if not steps_path.exists():
            raise FileNotFoundError(f"Steps file not found: {steps_path}")

        with open(steps_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # 使用pydantic驗證配置
        try:
            return StepsConfig.model_validate(data)
        except Exception as e:
            raise ValueError(f"Invalid steps.yaml format: {e}") from e

    def _create_env_modules(
        self, env_module_types: List[str], env_kwargs: Dict[str, Dict[str, Any]]
    ) -> List[EnvBase]:
        """建立環境模組例項"""
        env_modules = []
        env_type_map = {
            module_type: env_class
            for module_type, env_class in get_registered_env_modules()
        }

        for module_type in env_module_types:
            if module_type not in env_type_map:
                raise ValueError(
                    f"Environment module type '{module_type}' not found in registry. "
                    f"Available types: {list(env_type_map.keys())}"
                )

            env_class = env_type_map[module_type]
            module_kwargs = env_kwargs.get(module_type, {})
            env_module = env_class(**module_kwargs)
            env_modules.append(env_module)

        return env_modules

    def _create_agents(
        self,
        agent_args: List[Dict[str, Any]],
    ) -> List[AgentBase]:
        """建立 agent 例項。"""
        agents = []
        agent_type_map = {
            agent_type: agent_class
            for agent_type, agent_class in get_registered_agent_modules()
        }

        for agent_arg in agent_args:
            agent_type = agent_arg.get("agent_type")
            agent_id = agent_arg.get("agent_id")

            if not agent_type:
                raise ValueError(f"Agent config missing agent_type: {agent_arg}")

            if agent_id is None:
                raise ValueError(f"Agent config missing agent_id: {agent_arg}")

            if agent_type not in agent_type_map:
                raise ValueError(
                    f"Agent type '{agent_type}' not found in registry. "
                    f"Available types: {list(agent_type_map.keys())}"
                )

            agent_class = agent_type_map[agent_type]

            if "kwargs" not in agent_arg:
                raise ValueError(f"Agent config missing 'kwargs' field: {agent_arg}")

            init_kwargs = agent_arg["kwargs"].copy()

            if "id" not in init_kwargs:
                init_kwargs["id"] = int(agent_id)
            else:
                init_kwargs["id"] = int(init_kwargs["id"])

            agent = agent_class(**init_kwargs)
            agents.append(agent)

        return agents

    def _update_pid_file(self, status: str, **kwargs):
        """更新 pid.json 檔案"""
        # 讀取現有資料以保留進度資訊
        pid_data = {}
        if self.pid_file.exists():
            try:
                with open(self.pid_file, "r", encoding="utf-8") as f:
                    pid_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # 更新基本欄位
        pid_data.update(
            {
                "pid": os.getpid(),
                "status": status,
                "start_time": pid_data.get(
                    "start_time", datetime.now(timezone.utc).isoformat()
                ),
                **kwargs,
            }
        )

        if status == "completed" or status == "failed":
            pid_data["end_time"] = datetime.now(timezone.utc).isoformat()

        with open(self.pid_file, "w", encoding="utf-8") as f:
            json.dump(pid_data, f, indent=2, ensure_ascii=False)

    def _update_progress(self):
        """更新模擬進度到 pid.json"""
        if self.society:
            progress_data = {
                "simulation_time": self.society.current_time.isoformat(),
                "step_count": self.society.step_count,
            }
            self._update_pid_file("running", **progress_data)

    async def run(
        self,
        config_path: Path,
        steps_path: Path,
        experiment_id: Optional[str] = None,
    ):
        """
        執行實驗

        Args:
            config_path: 配置檔案路徑（init_config.json）
            steps_path: steps.yaml 檔案路徑
            experiment_id: 實驗ID（可選）
        """
        try:
            # 驗證環境變數（必須在任何操作之前）
            self._validate_environment()

            # 更新狀態為執行中
            self._update_pid_file("running", experiment_id=experiment_id)

            # 載入配置
            logger.info(f"Loading config from {config_path}")
            config = self._load_config(config_path)

            # 提取配置資訊（現在config是InitConfig模型）
            env_modules_config = config.env_modules
            agent_configs = config.agents
            env_module_types = [m.module_type for m in env_modules_config]
            env_kwargs = {m.module_type: m.kwargs for m in env_modules_config}

            # 轉換agent配置為字典格式（用於_create_agents方法）
            agent_args = [
                {
                    "agent_id": agent.agent_id,
                    "agent_type": agent.agent_type,
                    "kwargs": agent.kwargs,
                }
                for agent in agent_configs
            ]

            # 載入步驟配置
            logger.info(f"Loading steps from {steps_path}")
            steps_config = self._load_steps(steps_path)

            start_t = datetime.fromisoformat(steps_config.start_t)
            steps = steps_config.steps

            # 掃描並註冊自定義模組（在建立環境模組之前）
            workspace_path = self.run_dir.resolve()
            # 向上查詢包含 custom/ 目錄的工作區根目錄
            custom_root = workspace_path
            while custom_root.parent != custom_root:
                if (custom_root / "custom").is_dir():
                    break
                custom_root = custom_root.parent
            if (custom_root / "custom").is_dir():
                logger.info(f"Scanning custom modules from {custom_root}")
                scan_and_register_custom_modules(custom_root)
            else:
                logger.info("No custom/ directory found, skipping custom module scan")

            # 建立環境模組
            logger.info("Creating environment modules...")
            env_modules = self._create_env_modules(env_module_types, env_kwargs)

            # 若啟用回放則先建立並初始化 ReplayWriter，再傳入 env router
            replay_writer: Optional[ReplayWriter] = None
            if self.run_dir is not None:
                replay_writer = ReplayWriter(self.run_dir / "sqlite.db")
                await replay_writer.init()
                logger.info("ReplayWriter initialized")

            env_router = CodeGenRouter(
                env_modules=env_modules,
                replay_writer=replay_writer,
                final_summary_enabled=config.codegen_router.final_summary_enabled,
            )
            # Expose experiment root to helpers/skills
            env_router.run_dir = self.run_dir.resolve()

            logger.info(f"Creating {len(agent_args)} agents...")
            agents = self._create_agents(agent_args)

            logger.info("Creating AgentSociety instance...")
            self.society = AgentSociety(
                agents=agents,
                env_router=env_router,
                start_t=start_t,
                run_dir=self.run_dir,
                enable_replay=True,
                replay_writer=replay_writer,
            )

            await self.society.init()
            logger.info("AgentSociety initialized")

            # 執行步驟
            logger.info(f"Executing {len(steps)} steps...")

            for step_idx, step in enumerate(steps):
                if self._should_terminate:
                    logger.info("Termination requested, stopping execution")
                    break

                step_type = step.type

                # 更新進度到 pid.json
                self._update_progress()

                try:
                    if isinstance(step, RunStep):
                        logger.info(
                            f"Running {step.num_steps} steps with tick={step.tick}"
                        )

                        # 建立定期更新進度的任務
                        async def update_progress_periodically():
                            while not self._should_terminate:
                                await asyncio.sleep(1)  # 每秒更新一次
                                if self.society and not self._should_terminate:
                                    self._update_progress()

                        progress_task = asyncio.create_task(
                            update_progress_periodically()
                        )
                        try:
                            await self.society.run(
                                num_steps=step.num_steps, tick=step.tick
                            )
                        finally:
                            progress_task.cancel()
                            try:
                                await progress_task
                            except asyncio.CancelledError:
                                pass
                        # 最終更新進度
                        self._update_progress()

                    elif isinstance(step, AskStep):
                        logger.info(f"Asking: {step.question}")
                        answer = await self.society.ask(step.question)
                        logger.info(f"Answer: {answer}")

                        # 儲存結果到artifacts目錄，使用模擬時間作為檔案命名，Markdown格式
                        sim_time = self.society.current_time
                        timestamp = sim_time.strftime("%Y%m%d_%H%M%S")
                        artifact_file = (
                            self.artifacts_dir / f"ask_step_{step_idx}_{timestamp}.md"
                        )
                        with open(artifact_file, "w", encoding="utf-8") as f:
                            # YAML front matter
                            f.write("---\n")
                            f.write(
                                f"question: {yaml.dump(step.question, allow_unicode=True, default_flow_style=False).rstrip()}\n"
                            )
                            f.write("---\n\n")
                            # Markdown content
                            f.write(f"{answer}\n")
                        logger.info(f"Ask result saved to {artifact_file}")

                    elif isinstance(step, InterveneStep):
                        logger.info(f"Intervening: {step.instruction}")
                        intervene_result = await self.society.intervene(
                            step.instruction
                        )
                        logger.info(f"Result: {intervene_result}")

                        # 儲存結果到artifacts目錄，使用模擬時間作為檔案命名，Markdown格式
                        sim_time = self.society.current_time
                        timestamp = sim_time.strftime("%Y%m%d_%H%M%S")
                        artifact_file = (
                            self.artifacts_dir
                            / f"intervene_step_{step_idx}_{timestamp}.md"
                        )
                        with open(artifact_file, "w", encoding="utf-8") as f:
                            # YAML front matter
                            f.write("---\n")
                            f.write(
                                f"instruction: {yaml.dump(step.instruction, allow_unicode=True, default_flow_style=False).rstrip()}\n"
                            )
                            f.write("---\n\n")
                            # Markdown content
                            f.write(f"{intervene_result}\n")
                        logger.info(f"Intervene result saved to {artifact_file}")

                    elif isinstance(step, QuestionnaireStep):
                        questionnaire = Questionnaire(
                            questionnaire_id=step.questionnaire_id,
                            title=step.title or "",
                            description=step.description or "",
                            questions=step.questions,
                        )
                        logger.info(
                            "Running questionnaire %s with %s questions",
                            questionnaire.questionnaire_id,
                            len(questionnaire.questions),
                        )
                        questionnaire_result = await self.society.run_questionnaire(
                            questionnaire,
                            target_agent_ids=step.target_agent_ids,
                        )

                        sim_time = self.society.current_time
                        timestamp = sim_time.strftime("%Y%m%d_%H%M%S")
                        artifact_file = (
                            self.artifacts_dir
                            / f"questionnaire_step_{step_idx}_{timestamp}.json"
                        )
                        with open(artifact_file, "w", encoding="utf-8") as f:
                            json.dump(
                                questionnaire_result.model_dump(mode="json"),
                                f,
                                indent=2,
                                ensure_ascii=False,
                            )
                        logger.info(
                            "Questionnaire result saved to %s",
                            artifact_file,
                        )

                    else:
                        logger.warning(f"Unknown step type: {step_type}, skipping")
                        continue

                    # 更新進度到 pid.json（步驟完成）
                    self._update_progress()

                except Exception as e:
                    logger.error(
                        f"Error executing step {step_idx} ({step_type}): {e}",
                        exc_info=True,
                    )

                    # 更新進度到 pid.json（步驟失敗）
                    self._update_progress()

            # 關閉society
            await self.society.close()
            logger.info("Experiment completed successfully")
            self._update_pid_file("completed")

        except Exception as e:
            logger.error(f"Experiment failed: {e}", exc_info=True)
            self._update_pid_file("failed", error=str(e))
            raise


def main():
    """命令列入口"""
    # 早期環境變數驗證（在任何操作之前）
    _validate_env_early()

    parser = argparse.ArgumentParser(
        description="Run AgentSociety2 simulation experiment"
    )
    parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to configuration file (init_config.json)",
    )
    parser.add_argument(
        "--steps",
        type=str,
        required=True,
        help="Path to steps.yaml file",
    )
    parser.add_argument(
        "--run-dir",
        type=str,
        help="Path to run/ directory (default: current directory)",
        default=".",
    )
    parser.add_argument(
        "--experiment-id",
        type=str,
        help="Experiment ID (optional)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        help="Path to log file (optional). If not specified, logs go to stdout/stderr only.",
    )

    args = parser.parse_args()

    # 設定日誌級別
    set_logger_level(args.log_level)

    # 設定日誌檔案
    if args.log_file:
        add_file_handler(args.log_file, level=args.log_level)

    config_path = Path(args.config).resolve()
    steps_path = Path(args.steps).resolve()
    run_dir = Path(args.run_dir).resolve()

    # 驗證檔案存在
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    if not steps_path.exists():
        print(f"Error: Steps file not found: {steps_path}", file=sys.stderr)
        sys.exit(1)

    # 執行實驗
    runner = ExperimentRunner(run_dir=run_dir)
    try:
        asyncio.run(
            runner.run(
                config_path=config_path,
                steps_path=steps_path,
                experiment_id=args.experiment_id,
            )
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Failed to run experiment: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
