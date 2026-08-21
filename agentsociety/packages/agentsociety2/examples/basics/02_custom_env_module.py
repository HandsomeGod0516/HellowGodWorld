"""
Custom Environment Module Example

This example shows how to create a custom environment module
and run it using AgentSociety.
"""

import asyncio
from datetime import datetime
from typing import Dict
from agentsociety2 import PersonAgent
from agentsociety2.env import EnvBase, tool, CodeGenRouter
from agentsociety2.society import AgentSociety


class WeatherEnvironment(EnvBase):
    """A simple weather environment module."""

    def __init__(self):
        super().__init__()
        # Internal state
        self._weather = "sunny"
        self._temperature = 25
        self._agent_locations: Dict[int, str] = {}

    @tool(readonly=True, kind="observe")
    def get_weather(self, agent_id: int) -> str:
        """Get the current weather for an agent's location."""
        location = self._agent_locations.get(agent_id, "unknown location")
        return f"The weather in {location} is {self._weather} with {self._temperature}°C."

    @tool(readonly=False)
    def change_weather(self, weather: str, temperature: int) -> str:
        """Change the weather conditions."""
        self._weather = weather
        self._temperature = temperature
        return f"Weather changed to {weather} at {temperature}°C."

    @tool(readonly=False)
    def set_agent_location(self, agent_id: int, location: str) -> str:
        """Set an agent's location."""
        self._agent_locations[agent_id] = location
        return f"Agent {agent_id} is now in {location}."

    @tool(readonly=True, kind="statistics")
    def get_average_temperature(self) -> str:
        """Get the current average temperature."""
        return f"The current temperature is {self._temperature}°C."


async def main():
    # Create environment module
    weather_module = WeatherEnvironment()

    # Create environment router
    env_router = CodeGenRouter(env_modules=[weather_module])

    # Create agents
    agents = [
        PersonAgent(id=i, profile={"name": f"Agent{i}", "personality": "curious"})
        for i in range(1, 3)
    ]

    # Create the society
    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=datetime.now(),
    )
    await society.init()

    print("=== Custom Environment Module ===\n")

    # Query: Get current state
    print("1. What's the current environment state?")
    response = await society.ask("What's the current weather and temperature?")
    print(f"Answer: {response}\n")

    # Intervene: Change state
    print("2. Change the weather to rainy, 18°C")
    response = await society.intervene(
        "Change the weather to rainy and set temperature to 18 degrees Celsius"
    )
    print(f"Result: {response}\n")

    # Query: Verify change
    print("3. What's the weather now?")
    response = await society.ask("What's the current temperature?")
    print(f"Answer: {response}\n")

    await society.close()


if __name__ == "__main__":
    asyncio.run(main())
