"""Questionnaire runtime for agent-facing external surveys."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Sequence

import json_repair
from pydantic import BaseModel, Field

from agentsociety2.agent import AgentBase
from agentsociety2.society.models import QuestionItem

__all__ = [
    "AgentQuestionnaireResult",
    "Questionnaire",
    "QuestionnaireAnswer",
    "QuestionnaireResponse",
    "QuestionnaireRunner",
]


class Questionnaire(BaseModel):
    """Runtime questionnaire definition."""

    questionnaire_id: str = Field(..., min_length=1)
    title: str = ""
    description: str = ""
    questions: list[QuestionItem] = Field(..., min_length=1)


class QuestionnaireAnswer(BaseModel):
    """Single parsed answer for one question."""

    question_id: str
    raw_text: str
    raw_response: str | None = None
    parsed_value: Any = None
    reason: str | None = None
    parse_success: bool = True
    parse_error: str | None = None


class AgentQuestionnaireResult(BaseModel):
    """Collected questionnaire answers for one agent."""

    agent_id: int
    agent_name: str
    answers: list[QuestionnaireAnswer]


class QuestionnaireResponse(BaseModel):
    """Top-level questionnaire execution result."""

    questionnaire_id: str
    title: str = ""
    description: str = ""
    simulation_time: str
    step_count: int
    target_agent_ids: list[int]
    questions: list[QuestionItem]
    responses: list[AgentQuestionnaireResult]


def _build_question_prompt(questionnaire: Questionnaire, question: QuestionItem) -> str:
    lines = []
    if questionnaire.title:
        lines.append(f"Questionnaire Title: {questionnaire.title}")
    if questionnaire.description:
        lines.append(f"Questionnaire Description: {questionnaire.description}")
    lines.append(f"Question ID: {question.id}")
    lines.append(f"Question: {question.prompt}")
    lines.append("")
    lines.append("Answer as yourself based on your own current beliefs, plans, emotions, and memory.")
    lines.append(
        'Return valid JSON with exactly two top-level fields: '
        '"answer" and "reason".'
    )
    lines.append(
        '"reason" should briefly explain why you gave that answer, grounded in your own beliefs, memory, plans, or emotions.'
    )

    if question.response_type == "integer":
        lines.append('"answer" must be one integer.')
    elif question.response_type == "float":
        lines.append('"answer" must be one number.')
    elif question.response_type == "choice":
        options = ", ".join(question.choices)
        lines.append(f"Options: {options}")
        lines.append('"answer" must be one option exactly as written.')
    elif question.response_type == "json":
        lines.append('"answer" must itself be valid JSON that directly answers the question.')
    else:
        lines.append('"answer" must be concise plain text.')

    return "\n".join(lines).strip()


def _coerce_reason(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    stringified = str(value).strip()
    return stringified or None


def _stringify_answer_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value).strip()


def _parse_question_answer(question: QuestionItem, raw_text: str) -> QuestionnaireAnswer:
    full_response = (raw_text or "").strip()
    answer_payload: Any = full_response
    answer_text = full_response
    reason: str | None = None
    structured_payload: Any = None

    try:
        structured_payload = json_repair.loads(full_response)
    except Exception:
        structured_payload = None

    if isinstance(structured_payload, dict) and "answer" in structured_payload:
        answer_payload = structured_payload.get("answer")
        answer_text = _stringify_answer_value(answer_payload)
        reason = _coerce_reason(structured_payload.get("reason"))

    parsed_value: Any = answer_text
    parse_success = True
    parse_error: str | None = None

    try:
        if question.response_type == "integer":
            if isinstance(answer_payload, bool):
                raise ValueError("boolean is not a valid integer answer")
            if isinstance(answer_payload, int):
                parsed_value = answer_payload
            elif isinstance(answer_payload, float) and answer_payload.is_integer():
                parsed_value = int(answer_payload)
            else:
                match = re.search(r"-?\d+", answer_text)
                if match is None:
                    raise ValueError("no integer found")
                parsed_value = int(match.group(0))
        elif question.response_type == "float":
            if isinstance(answer_payload, bool):
                raise ValueError("boolean is not a valid numeric answer")
            if isinstance(answer_payload, (int, float)):
                parsed_value = float(answer_payload)
            else:
                match = re.search(r"-?\d+(?:\.\d+)?", answer_text)
                if match is None:
                    raise ValueError("no number found")
                parsed_value = float(match.group(0))
        elif question.response_type == "choice":
            normalized = answer_text.casefold()
            exact = next((choice for choice in question.choices if choice.casefold() == normalized), None)
            if exact is None:
                matches = [
                    choice
                    for choice in question.choices
                    if choice.casefold() in normalized or normalized in choice.casefold()
                ]
                if len(matches) == 1:
                    exact = matches[0]
            if exact is None:
                raise ValueError(f"answer does not match any choice: {question.choices}")
            parsed_value = exact
        elif question.response_type == "json":
            if isinstance(structured_payload, dict) and "answer" in structured_payload:
                if isinstance(answer_payload, str):
                    parsed_value = json_repair.loads(answer_payload)
                else:
                    parsed_value = answer_payload
            else:
                parsed_value = json_repair.loads(answer_text)
        else:
            parsed_value = answer_text
    except Exception as exc:
        parse_success = False
        parse_error = str(exc)
        parsed_value = answer_text

    return QuestionnaireAnswer(
        question_id=question.id,
        raw_text=answer_text,
        raw_response=full_response,
        parsed_value=parsed_value,
        reason=reason,
        parse_success=parse_success,
        parse_error=parse_error,
    )


class QuestionnaireRunner:
    """Execute a questionnaire against selected agents."""

    async def run(
        self,
        questionnaire: Questionnaire,
        agents: Sequence[AgentBase],
        *,
        t: datetime,
        step_count: int,
        target_agent_ids: list[int] | None = None,
    ) -> QuestionnaireResponse:
        by_id = {agent.id: agent for agent in agents}
        if target_agent_ids is None:
            selected_agents = list(agents)
            target_ids = [agent.id for agent in selected_agents]
        else:
            missing = [agent_id for agent_id in target_agent_ids if agent_id not in by_id]
            if missing:
                raise ValueError(f"Unknown target_agent_ids: {missing}")
            selected_agents = [by_id[agent_id] for agent_id in target_agent_ids]
            target_ids = list(target_agent_ids)

        answers_by_agent: dict[int, list[QuestionnaireAnswer]] = {
            agent.id: [] for agent in selected_agents
        }

        for question in questionnaire.questions:
            prompt = _build_question_prompt(questionnaire, question)
            raw_answers = await self._ask_agents(
                agents=selected_agents,
                prompt=prompt,
                t=t,
            )
            for agent, raw_answer in zip(selected_agents, raw_answers):
                answers_by_agent[agent.id].append(_parse_question_answer(question, raw_answer))

        return QuestionnaireResponse(
            questionnaire_id=questionnaire.questionnaire_id,
            title=questionnaire.title,
            description=questionnaire.description,
            simulation_time=t.isoformat(),
            step_count=step_count,
            target_agent_ids=target_ids,
            questions=questionnaire.questions,
            responses=[
                AgentQuestionnaireResult(
                    agent_id=agent.id,
                    agent_name=agent.name,
                    answers=answers_by_agent[agent.id],
                )
                for agent in selected_agents
            ],
        )

    async def _ask_agents(
        self,
        *,
        agents: Sequence[AgentBase],
        prompt: str,
        t: datetime,
    ) -> list[str]:
        coroutines = [
            agent.answer_external_question(
                prompt,
                t=t,
                response_type="json",
            )
            for agent in agents
        ]
        return list(await asyncio.gather(*coroutines))
