"""
Multi-Router Example

This example shows how to use different router strategies
for agent-environment interaction with AgentSociety.
"""

import asyncio
from datetime import datetime
from agentsociety2 import PersonAgent
from agentsociety2.env import ReActRouter, PlanExecuteRouter, CodeGenRouter
from agentsociety2.contrib.env import SimpleSocialSpace
from agentsociety2.society import AgentSociety


async def demonstrate_router(router_class, router_name, question: str) -> str:
    """Helper to demonstrate different routers using AgentSociety."""
    # Create agent first (needed for SimpleSocialSpace)
    agent = PersonAgent(
        id=1,
        profile={
            "name": "Tester",
            "personality": "analytical and helpful",
        }
    )

    # Create environment with specific router
    env_router = router_class()
    env_router.register_module(
        SimpleSocialSpace(agent_id_name_pairs=[(agent.id, agent.name)])
    )

    # Create society
    society = AgentSociety(
        agents=[agent],
        env_router=env_router,
        start_t=datetime.now(),
    )
    await society.init()

    print(f"\n--- {router_name} ---")
    print(f"Question: {question}")

    response = await society.ask(question)
    print(f"Response: {response[:200]}...")

    await society.close()
    return response


async def main():
    print("=== Multi-Router Comparison ===\n")

    # Test question that requires different reasoning approaches
    question = (
        "I have 10 apples. I give 3 to Alice and 2 to Bob. "
        "Then Alice gives me back 1 apple. How many apples do I have now? "
        "Show your work step by step."
    )

    # ReAct Router: Reason + Act iteratively
    await demonstrate_router(
        ReActRouter,
        "ReAct Router (Reasoning + Acting)",
        question
    )

    # Plan-Execute Router: Plan first, then execute
    await demonstrate_router(
        PlanExecuteRouter,
        "Plan-Execute Router (Plan then Execute)",
        question
    )

    # CodeGen Router: Generate code to solve
    await demonstrate_router(
        CodeGenRouter,
        "Code Generation Router (Generate Python Code)",
        question
    )

    print("\n=== Multi-Router for Complex Tasks ===\n")

    # More complex scenario
    complex_task = (
        "Simulate a small auction where 3 people bid on an item. "
        "Start at $10, increment by $5, and stop after 3 rounds. "
        "Tell me who wins and at what price."
    )

    await demonstrate_router(
        PlanExecuteRouter,
        "Plan-Execute for Auction Simulation",
        complex_task
    )

    print("\n=== Comparison Summary ===")
    print("""
- ReAct Router: Good for tasks requiring iterative reasoning and action
- Plan-Execute Router: Good for complex multi-step tasks with clear goals
- CodeGen Router: Good for tasks that can be solved with code/computation
    """)


if __name__ == "__main__":
    asyncio.run(main())
