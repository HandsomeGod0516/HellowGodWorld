"""
Basic Agent Example: Hello Agent

This example shows how to run a simple agent simulation using AgentSociety.
"""

import asyncio
from datetime import datetime
from agentsociety2 import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.contrib.env import SimpleSocialSpace
from agentsociety2.society import AgentSociety


async def main():
    # Create agent first (we need agent info for SimpleSocialSpace)
    agent = PersonAgent(
        id=1,
        profile={
            "name": "Alice",
            "age": 28,
            "personality": "friendly, curious, and optimistic",
            "bio": "A software engineer who loves hiking, reading sci-fi novels, and cooking.",
            "location": "San Francisco",
        }
    )

    # Create environment module with agent info
    social_env = SimpleSocialSpace(
        agent_id_name_pairs=[(agent.id, agent.name)]
    )

    # Create environment router
    env_router = CodeGenRouter(env_modules=[social_env])

    # Create the society (recommended way to run experiments)
    society = AgentSociety(
        agents=[agent],
        env_router=env_router,
        start_t=datetime.now(),
    )

    # Initialize (sets up agents with environment)
    await society.init()

    print("=== Basic Agent Interaction ===\n")

    # Ask questions through the society
    questions = [
        "What's the name of all agents?",
        "Tell me about Alice's personality and interests.",
        "What agents exist in this simulation?",
    ]

    for question in questions:
        print(f"Question: {question}")
        response = await society.ask(question)
        print(f"Answer: {response}\n")

    # Close the society
    await society.close()


if __name__ == "__main__":
    asyncio.run(main())
