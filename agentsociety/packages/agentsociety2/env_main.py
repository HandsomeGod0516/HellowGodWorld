# ruff: noqa: F841

import asyncio
import os
from datetime import datetime, timedelta
from typing import cast

from agentsociety2.contrib.env.global_information import GlobalInformationEnv
from agentsociety2.contrib.env.mobility_space import MobilitySpace
from agentsociety2.env import EnvBase, EnvLLM, CodeGenRouter
from agentsociety2.config import (
    AgentLLMConfig,
    EnvLLMConfig,
    HelperLLMConfig,
    LLMConfig,
)
from dotenv import load_dotenv
from litellm.router import Router

load_dotenv()


async def main():
    # 1) Config: 1s tick, run for 60s
    start_t = datetime.now()
    end_t = start_t + timedelta(seconds=10)

    config = LLMConfig(
        model_list=[
            {
                "model_name": "glm-4.7",
                "litellm_params": {
                    "model": "openai/glm-4.7",
                    "api_key": os.getenv("API_KEY"),
                    "api_base": "https://llmapi.fiblab.net",
                },
            },
            {
                "model_name": "qwen2.5-14b",
                "litellm_params": {
                    "model": "openai/qwen2.5-14b-instruct",
                    "api_key": os.getenv("API_KEY"),
                    "api_base": "https://llmapi.fiblab.net",
                },
            },
            {
                "model_name": "bge-m3",
                "litellm_params": {
                    "model": "openai/bge-m3",
                    "api_key": os.getenv("API_KEY"),
                    "api_base": "https://llmapi.fiblab.net",
                },
            },
        ],
        agent=AgentLLMConfig(
            model_name="qwen2.5-14b",
        ),
        env=EnvLLMConfig(
            codegen_model_name="glm-4.7",
            summary_model_name="qwen2.5-14b",
            embedding_model_name="bge-m3",
            embedding_size=1024,
        ),
        helper=HelperLLMConfig(
            model_name="glm-4.7",
        ),
    )

    # 2) Environment: only GlobalInformationEnv, also used as fallback
    env_llm = EnvLLM(
        router=Router(model_list=config.model_list, cache_responses=True),
        codegen_model_name=config.env.codegen_model_name,
        summary_model_name=config.env.summary_model_name,
        embedding_model_name=config.env.embedding_model_name,
        embedding_size=config.env.embedding_size,
    )
    global_info = GlobalInformationEnv()
    mobility = MobilitySpace(
        os.getenv("MOBILITY_MAP_PATH"),
        os.getenv("MOBILITY_HOME_DIR", os.path.expanduser("~/.agentsociety")),
        [
            {
                "id": 1,
                "position": {
                    "kind": "aoi",
                    "aoi_id": 5_0000_0000,
                },
            },
            {
                "id": 2,
                "position": {
                    "kind": "aoi",
                    "aoi_id": 5_0000_0001,
                },
            },
        ],
    )

    env_modules = cast(list[EnvBase], [mobility, global_info])

    env_router = CodeGenRouter(env_modules=env_modules)
    # print("--------------------------------")
    # print(env_router._readonly_tools_xml)
    print("--------------------------------")
    print(env_router._writable_tools_xml)
    print("--------------------------------")
    await env_router.init(start_t)
    ctx, answer = await env_router.ask(
        {"id": 1},
        "Go to restaurant",
        readonly=False,
    )
    print(answer)

    await env_router.step(100, start_t + timedelta(seconds=100))
    ctx, answer = await env_router.ask(
        {"id": 1},
        "go to restaurant",
        readonly=False,
    )
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
