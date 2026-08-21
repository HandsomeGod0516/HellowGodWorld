"""
Global Information Environment
This environment provides global information to the agent.
"""

import asyncio
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field

from agentsociety2.env import (
    EnvBase,
    tool,
)
from agentsociety2.storage import ColumnDef


class GetGlobalInformationResponse(BaseModel):
    """Response model for get() function"""

    global_information: str = Field(..., description="The global information")


class SetGlobalInformationResponse(BaseModel):
    """Response model for set() function"""

    old_information: str = Field(..., description="The old information")
    new_information: str = Field(..., description="The new information")


class GlobalInformationEnv(EnvBase):
    _env_state_columns: ClassVar[list[ColumnDef]] = [
        ColumnDef("global_information", "TEXT", nullable=False),
    ]

    def __init__(self):
        """
        Initialize the Global Information Environment.
        """
        super().__init__()
        self._default_global_information = (
            "It's a normal day without any special events."
        )
        self._global_information = self._default_global_information
        self._lock = asyncio.Lock()
        self._step_counter: int = 0

    @classmethod
    def mcp_description(cls) -> str:
        """
        Return a description text for MCP environment module candidate list.
        Includes parameter descriptions.
        """
        description = f"""{cls.__name__}: Global information environment module.

**Description:** Provides global information to the agent like weather, global news, etc.

**Initialization Parameters (excluding llm):**
No additional parameters required. This module only requires the llm parameter.

**Example initialization config:**
```json
{{}}
```
"""
        return description

    @property
    def description(self):
        """Description of the environment module for router selection and function calling"""
        return """You are a global information environment module specialized in providing global information to the agent.

Your task is to use the get and set functions with context paths to provide global information to the agent."""

    @tool(readonly=True, kind="observe")
    async def get(self) -> GetGlobalInformationResponse:
        """
        Get the global information.

        Returns:
            The global information.
        """
        async with self._lock:
            return GetGlobalInformationResponse(
                global_information=self._global_information
            )

    @tool(readonly=False)
    async def set(self, prompt: str) -> SetGlobalInformationResponse:
        """
        Set the global information.

        Args:
            prompt: The global information.

        Returns:
            The global information.
        """
        async with self._lock:
            old_information = self._global_information
            self._global_information = prompt
        return SetGlobalInformationResponse(
            old_information=old_information,
            new_information=prompt,
        )

    async def init(self, start_datetime: datetime):
        await super().init(start_datetime)
        async with self._lock:
            self._global_information = self._default_global_information
            self._step_counter = 0

    async def step(self, tick: int, t: datetime):
        """
        Run forward one step.

        Args:
            tick: The number of ticks of this simulation step.
            t: The current datetime of the simulation after this step with the ticks.
        """
        async with self._lock:
            self.t = t
            global_information = self._global_information

        await self._write_env_state(
            step=self._step_counter,
            t=t,
            global_information=global_information,
        )
        self._step_counter += 1

    def _dump_state(self) -> dict:
        return {
            "global_information": self._global_information,
            "step_counter": self._step_counter,
        }

    def _load_state(self, state: dict):
        self._global_information = state.get(
            "global_information", self._default_global_information
        )
        self._step_counter = state.get("step_counter", 0)
