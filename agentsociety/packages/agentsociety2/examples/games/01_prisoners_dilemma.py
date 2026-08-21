"""
Prisoner's Dilemma Game Example

This example demonstrates a classic game theory scenario
using AgentSociety to coordinate agents and environment.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from agentsociety2 import PersonAgent
from agentsociety2.env import CodeGenRouter
from agentsociety2.contrib.env import PrisonersDilemma
from agentsociety2.storage import ReplayWriter
from agentsociety2.society import AgentSociety


async def main():
    # Setup replay writer
    writer = ReplayWriter(Path("prisoners_dilemma.db"))
    await writer.init()

    print("=== Prisoner's Dilemma ===\n")
    print("Two agents are arrested and held in separate rooms.")
    print("Each can either Cooperate (stay silent) or Defect (betray the other).\n")

    # Create the game environment
    game = PrisonersDilemma(
        cooperate_reward=1,      # Reward if both cooperate
        defect_punishment=1,      # Punishment if both defect
        temptation=3,             # Temptation to defect (reward)
        sucker_punishment=3,      # Sucker's payoff (punishment)
    )

    # Create two players
    alice = PersonAgent(
        id=1,
        profile={
            "name": "Alice",
            "personality": "strategic and rational",
            "strategy": "will analyze the situation carefully",
        },
    )

    bob = PersonAgent(
        id=2,
        profile={
            "name": "Bob",
            "personality": "trusting but cautious",
            "strategy": "prefers cooperation but wary of betrayal",
        },
    )

    # Create the society
    society = AgentSociety(
        agents=[alice, bob],
        env_router=CodeGenRouter(env_modules=[game]),
        start_t=datetime.now(),
        replay_writer=writer,
    )
    await society.init()

    # Explain the game
    print("Game Rules:")
    print("- If both Cooperate: Both get 1 year (light sentence)")
    print("- If both Defect: Both get 3 years (medium sentence)")
    print("- If one Cooperates and other Defects:")
    print("  * Cooperator gets 5 years (heavy sentence - the 'sucker')")
    print("  * Defector goes free (0 years - the 'temptation')\n")

    # Get their decisions
    print("Getting decisions from both players...\n")

    alice_decision = await society.ask(
        "You are Alice. You are in a prisoner's dilemma. "
        "You can either COOPERATE (stay silent) or DEFECT (betray your partner). "
        "You don't know what your partner will choose. "
        "What is your decision? Please respond with either 'COOPERATE' or 'DEFECT' "
        "and explain your reasoning."
    )

    print(f"Alice's decision: {alice_decision}\n")

    bob_decision = await society.ask(
        "You are Bob. You are in a prisoner's dilemma. "
        "You can either COOPERATE (stay silent) or DEFECT (betray your partner). "
        "You don't know what your partner will choose. "
        "What is your decision? Please respond with either 'COOPERATE' or 'DEFECT' "
        "and explain your reasoning."
    )

    print(f"Bob's decision: {bob_decision}\n")

    # Parse decisions
    alice_cooperates = "cooperate" in alice_decision.lower()
    bob_cooperates = "cooperate" in bob_decision.lower()

    # Determine outcome
    print("=== Results ===")
    if alice_cooperates and bob_cooperates:
        alice_sentence = 1
        bob_sentence = 1
        outcome = "Both COOPERATED"
    elif alice_cooperates and not bob_cooperates:
        alice_sentence = 5
        bob_sentence = 0
        outcome = "Alice COOPERATED, Bob DEFECTED"
    elif not alice_cooperates and bob_cooperates:
        alice_sentence = 0
        bob_sentence = 5
        outcome = "Alice DEFECTED, Bob COOPERATED"
    else:
        alice_sentence = 3
        bob_sentence = 3
        outcome = "Both DEFECTED"

    print(f"Outcome: {outcome}")
    print(f"Alice's sentence: {alice_sentence} years")
    print(f"Bob's sentence: {bob_sentence} years\n")

    # Reflection questions
    print("=== Reflection ===")
    reflection = await society.ask(
        f"Alice, the results are in: {outcome}. You got {alice_sentence} years "
        f"and Bob got {bob_sentence} years. Are you satisfied with your decision? "
        "Would you make the same choice again? Explain your thinking."
    )
    print(f"Alice reflects: {reflection}\n")

    # Cleanup
    await society.close()
    print("Game data saved to: prisoners_dilemma.db")


if __name__ == "__main__":
    asyncio.run(main())
