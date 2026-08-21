"""
Public Goods Game Example

A classic economic game where agents decide how much to contribute
to a public good that benefits everyone.

This example uses AgentSociety to coordinate multiple agents.
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from agentsociety2 import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.contrib.env import PublicGoodsGame
from agentsociety2.storage import ReplayWriter
from agentsociety2.society import AgentSociety


async def main():
    writer = ReplayWriter(Path("public_goods.db"))
    await writer.init()

    print("=== Public Goods Game ===\n")
    print("Each agent has an endowment they can contribute to a public good.")
    print("Total contributions are multiplied and distributed equally.\n")

    # Create the game
    game = PublicGoodsGame(
        endowment=100,           # Each agent starts with $100
        contribution_factor=1.5,  # Total is multiplied by 1.5
    )

    # Create 4 agents with different personalities
    agents = []
    profiles = [
        {"name": "Alice", "personality": "altruistic and community-minded"},
        {"name": "Bob", "personality": "self-interested and rational"},
        {"name": "Charlie", "personality": "cautious and skeptical"},
        {"name": "Diana", "personality": "optimistic and trusting"},
    ]

    for i, profile in enumerate(profiles, 1):
        agent = PersonAgent(id=i, profile=profile)
        agents.append(agent)

    # Create the society
    society = AgentSociety(
        agents=agents,
        env_router=CodeGenRouter(env_modules=[game]),
        start_t=datetime.now(),
        replay_writer=writer,
    )
    await society.init()

    # Play 3 rounds
    for round_num in range(1, 4):
        print(f"\n--- Round {round_num} ---")

        contributions = {}
        for agent in agents:
            # Get contribution decision through society
            decision = await society.ask(
                f"You are {agent._name}. You have ${game.endowment}. How much will you contribute "
                f"to the public good (0-${game.endowment})? The total contributions "
                f"will be multiplied by {game.contribution_factor} and divided equally "
                f"among all {len(agents)} players. Explain your reasoning."
            )

            # Extract contribution amount (simple parsing)
            match = re.search(r'\$?(\d+)', decision)
            if match:
                amount = int(match.group(1))
                amount = min(max(amount, 0), game.endowment)  # Clamp to valid range
            else:
                amount = game.endowment // 2  # Default to half

            contributions[agent._id] = amount
            print(f"{agent._name}: contributes ${amount}")
            print(f"  Reasoning: {decision[:100]}...")

        # Calculate results
        total = sum(contributions.values())
        multiplied = int(total * game.contribution_factor)
        each_return = multiplied // len(agents)

        print("Round Results:")
        print(f"  Total contributions: ${total}")
        print(f"  After multiplier (${game.contribution_factor}x): ${multiplied}")
        print(f"  Each player receives: ${each_return}\n")

        # Payoffs
        for agent in agents:
            contribution = contributions[agent._id]
            payoff = (game.endowment - contribution) + each_return
            print(f"  {agent._name}: ${game.endowment} - ${contribution} + ${each_return} = ${payoff}")

    # Final reflection
    print("\n=== Final Reflection ===\n")
    final_thoughts = await society.ask(
        "The game is over. Reflect on the group's behavior. "
        "Did the group cooperate enough? What could have been done differently?"
    )
    print(f"Final thoughts: {final_thoughts}\n")

    await society.close()
    print("Game data saved to: public_goods.db")


if __name__ == "__main__":
    asyncio.run(main())
