"""
Custom Agent Example

This example shows how to create a custom agent by inheriting from AgentBase.
Custom agents can then be used with AgentSociety for coordinated experiments.
"""

import asyncio
from datetime import datetime
from agentsociety2.agent import AgentBase
from agentsociety2.env import CodeGenRouter
from agentsociety2.contrib.env import SimpleSocialSpace
from agentsociety2.society import AgentSociety


class SpecialistAgent(AgentBase):
    """
    A custom agent that specializes in a particular domain.
    """

    def __init__(self, id: int, profile: dict, specialty: str, **kwargs):
        super().__init__(id=id, profile=profile, **kwargs)
        self._specialty = specialty

    async def ask(self, question: str, readonly: bool = True) -> str:
        """
        Override ask to add specialty context to questions.
        """
        # Add specialty context to the question
        enhanced_question = (
            f"You are a specialist in {self._specialty}. "
            f"Answer the following question from this perspective: {question}"
        )

        # Call parent implementation with enhanced question
        return await super().ask(enhanced_question, readonly=readonly)

    async def reflect_on_specialty(self) -> str:
        """
        Custom method for reflecting on the agent's specialty.
        """
        question = (
            f"As a specialist in {self._specialty}, "
            "what do you consider to be the most important aspects of your field?"
        )
        return await super().ask(question, readonly=True)


class RecursiveAgent(AgentBase):
    """
    An agent that uses chain-of-thought reasoning by default.
    """

    async def ask(self, question: str, readonly: bool = True, depth: int = 2) -> str:
        """
        Override ask to implement multi-step reasoning.
        """
        if depth <= 0:
            return await super().ask(question, readonly=readonly)

        # First, ask the agent to break down the question
        breakdown_prompt = (
            f"Break down this question into sub-questions: {question}\n"
            "Respond with a JSON object containing a 'sub_questions' array."
        )

        breakdown = await super().ask(breakdown_prompt, readonly=True)

        # Process sub-questions recursively
        import json_repair

        try:
            parsed = json_repair.loads(breakdown)
            sub_questions = parsed.get("sub_questions", [])
        except Exception:
            sub_questions = []

        if not sub_questions:
            # If parsing failed, just answer normally
            return await super().ask(question, readonly=readonly)

        # Answer each sub-question
        sub_answers = []
        for sq in sub_questions[:3]:  # Limit to 3 sub-questions
            answer = await super().ask(sq, readonly=True)
            sub_answers.append(f"Q: {sq}\nA: {answer}")

        # Synthesize final answer
        synthesis_prompt = (
            f"Original question: {question}\n\n"
            f"Sub-question analysis:\n{chr(10).join(sub_answers)}\n\n"
            "Based on the above analysis, provide a comprehensive answer to the original question."
        )

        return await super().ask(synthesis_prompt, readonly=readonly)


async def main():
    print("=== Custom Agent Examples ===\n")

    # Example 1: Specialist Agent
    print("--- Specialist Agent ---\n")

    climate_specialist = SpecialistAgent(
        id=1,
        profile={"name": "Dr. Climate", "personality": "scientific and concerned"},
        specialty="climate science and environmental policy"
    )

    # Create environment with agent info
    social_env = SimpleSocialSpace(
        agent_id_name_pairs=[(climate_specialist.id, climate_specialist.name)]
    )
    env_router = CodeGenRouter(env_modules=[social_env])

    # Create society with specialist agent
    society1 = AgentSociety(
        agents=[climate_specialist],
        env_router=env_router,
        start_t=datetime.now(),
    )
    await society1.init()

    question = "What should cities do to prepare for extreme weather?"
    response = await society1.ask(question)
    print(f"Question: {question}")
    print(f"Specialist Response: {response[:200]}...\n")

    await society1.close()

    # Example 2: Using custom methods directly (requires agent to be initialized)
    print("--- Custom Method: Reflection ---\n")

    # Need to re-initialize for custom method demo
    specialist2 = SpecialistAgent(
        id=2,
        profile={"name": "Dr. Science", "personality": "curious"},
        specialty="environmental science"
    )

    social_env2 = SimpleSocialSpace(
        agent_id_name_pairs=[(specialist2.id, specialist2.name)]
    )
    env_router2 = CodeGenRouter(env_modules=[social_env2])

    society2 = AgentSociety(
        agents=[specialist2],
        env_router=env_router2,
        start_t=datetime.now(),
    )
    await society2.init()

    reflection = await specialist2.reflect_on_specialty()
    print(f"Specialty Reflection: {reflection[:200]}...\n")

    await society2.close()

    # Example 3: Recursive Agent with CoT
    print("--- Recursive (CoT) Agent ---\n")

    thinker = RecursiveAgent(
        id=3,
        profile={"name": "Deep Thinker", "personality": "analytical and methodical"}
    )

    social_env3 = SimpleSocialSpace(
        agent_id_name_pairs=[(thinker.id, thinker.name)]
    )
    env_router3 = CodeGenRouter(env_modules=[social_env3])

    society3 = AgentSociety(
        agents=[thinker],
        env_router=env_router3,
        start_t=datetime.now(),
    )
    await society3.init()

    question = "How can we reduce urban traffic congestion?"
    response = await thinker.ask(question, readonly=True, depth=2)
    print(f"Question: {question}")
    print(f"CoT Response: {response[:300]}...\n")

    await society3.close()


if __name__ == "__main__":
    asyncio.run(main())
