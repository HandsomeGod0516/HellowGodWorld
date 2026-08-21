"""
Replay System Example

This example shows how to use the ReplayWriter to track
and replay agent interactions running through AgentSociety.
"""

import asyncio
from datetime import datetime
from pathlib import Path

from agentsociety2 import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.contrib.env import SimpleSocialSpace
from agentsociety2.storage import ReplayWriter
from agentsociety2.society import AgentSociety


async def main():
    # Setup replay writer
    db_path = "example_replay.db"
    Path(db_path).unlink(missing_ok=True)

    writer = ReplayWriter(Path(db_path))
    await writer.init()

    print("=== Replay System Example ===\n")

    # Create agents first (we need agent info for SimpleSocialSpace)
    agents = [
        PersonAgent(
            id=i,
            profile={
                "name": f"Agent{i}",
                "personality": "friendly" if i % 2 == 0 else "curious",
            },
        )
        for i in range(1, 4)
    ]

    # Create environment module with agent info
    social_env = SimpleSocialSpace(
        agent_id_name_pairs=[(agent.id, agent.name) for agent in agents]
    )

    # Create environment router
    env_router = CodeGenRouter(env_modules=[social_env])
    env_router.set_replay_writer(writer)

    # Create the society with replay enabled
    society = AgentSociety(
        agents=agents,
        env_router=env_router,
        start_t=datetime.now(),
        replay_writer=writer,
    )
    await society.init()

    # Run interactions through the society
    print("Running agent interactions...\n")
    for agent in agents:
        question = f"Hello {agent._name}! Introduce yourself."
        response = await society.ask(question)
        print(f"{agent._name}: {response[:100]}...")

    # Cleanup
    await society.close()
    print("\nReplay database saved to:", db_path)
    print("Agent replay tables are no longer written; inspect environment replay datasets instead.")


if __name__ == "__main__":
    asyncio.run(main())
