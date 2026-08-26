import json
import os

import pytest
from crewai import Agent, LLM
from crewai.tasks.llm_guardrail import LLMGuardrail
from crewai.tasks.task_output import TaskOutput

from the_village.bridge import SessionBridge
from the_village.discussion.ai_speaker import _AiSpeaker
from the_village.state import GameState, Player

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "OPENAI_API_KEY" not in os.environ,
        reason="requires OPENAI_API_KEY to call a real LLM",
    ),
]


def _stub_agent() -> Agent:
    return Agent(role="Stub", goal="stub", backstory="stub")


def _make_state_with_dead_player() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="Corin", player_type="villager", is_alive=False),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _judge(description: str, text: str) -> tuple[bool, str | None]:
    llm = LLM(model=os.environ.get("MODEL", "gpt-4o-mini"))
    guardrail = LLMGuardrail(description=description, llm=llm)
    raw = json.dumps({"has_something_to_say": True, "text": text})
    output = TaskOutput(raw=raw, agent="A", description="speak", json_dict=None)
    return guardrail(output)


def test_guardrail_allows_generic_group_whereabouts_question():
    """Regression: with the pre-fix wording, this message -- which never
    names Corin -- was rejected by the real LLM judge on every retry, with
    a different invented rationale each time (turn-order commentary, a
    vague behavior claim, then that "everyone" implicates Corin)."""
    state = _make_state_with_dead_player()
    speaker = _AiSpeaker(state, SessionBridge(), "A", _stub_agent(), _stub_agent())
    description = speaker._build_guardrail_description()
    text = (
        "We need to talk to each other and figure out what everyone was "
        "doing last night."
    )
    passed, result = _judge(description, text)
    assert passed is True, result


def test_guardrail_rejects_explicit_dead_player_credibility_comparison():
    state = _make_state_with_dead_player()
    speaker = _AiSpeaker(state, SessionBridge(), "A", _stub_agent(), _stub_agent())
    description = speaker._build_guardrail_description()
    text = (
        "Della, I was at home last night, just like everyone else. But "
        "it's interesting how quickly you jumped to question me when we "
        "should be focusing on Corin's inconsistent story."
    )
    passed, result = _judge(description, text)
    assert passed is False
