from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from crewai import Agent

from the_village.core.bridge import SessionBridge
from the_village.discussion.ai_speaker import _GUARDRAIL_MODEL, _AiSpeaker, _SpeakerOutput
from the_village.discussion.speaker import DECLINED_TO_RESPOND
from the_village.discussion.villager_speaker import _VillagerSpeaker
from the_village.core.state import GameState, Player


def make_discussion_state() -> GameState:
    players = [
        Player(name="Dana", player_type="user"),
        Player(name="A", player_type="villager"),
        Player(name="B", player_type="villager"),
    ]
    return GameState(user_player_name="Dana", players=players)


def _stub_agent() -> Agent:
    """A minimal real Agent -- Task/Crew construction validates that `agent`
    fields are actual Agent instances, so a plain object() won't do, even
    though Crew.akickoff is mocked in these tests."""
    return Agent(role="Stub", goal="stub", backstory="stub")


def _crew_result(*pydantic_outputs):
    return SimpleNamespace(
        tasks_output=[SimpleNamespace(pydantic=p) for p in pydantic_outputs]
    )


def make_ai_speaker(state: GameState, player_name: str = "A") -> _AiSpeaker:
    """`_AiSpeaker` requires `_inner_prompt_instructions` from a concrete
    subclass -- `_VillagerSpeaker` stands in here since these tests exercise
    behavior shared by all speakers, not villager- or werewolf-specific
    wording."""
    return _VillagerSpeaker(
        state, SessionBridge(), player_name, _stub_agent(), _stub_agent()
    )


def test_speaker_output_has_addressed_to_field():
    assert "addressed_to" in _SpeakerOutput.model_fields


def test_guardrail_logs_text_and_reason_on_rejection(caplog):
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    text = "Bruce hasn't explained his whereabouts."
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(has_something_to_say=True, text=text)
    )
    with patch("the_village.discussion.ai_speaker.LLMGuardrail") as mock_cls:
        mock_cls.return_value = lambda o: (
            False,
            "Bruce is dead and out of the game.",
        )
        guardrail = speaker._build_guardrail()
        with caplog.at_level("WARNING"):
            passed, result = guardrail(output)
    assert passed is False
    assert "A" in caplog.text
    assert text in caplog.text
    assert "Bruce is dead and out of the game." in caplog.text


def test_guardrail_logs_increasing_attempt_number_across_retries(caplog):
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(has_something_to_say=True, text="hi")
    )
    with patch("the_village.discussion.ai_speaker.LLMGuardrail") as mock_cls:
        mock_cls.return_value = lambda o: (False, "nope")
        guardrail = speaker._build_guardrail()
        with caplog.at_level("WARNING"):
            guardrail(output)
            guardrail(output)
    assert "attempt 0" in caplog.text.lower()
    assert "attempt 1" in caplog.text.lower()


def test_guardrail_uses_the_guardrail_model_not_the_players_model():
    """The player's own model proved unreliable at following the guardrail's
    conditional rules faithfully, so judging is deliberately pinned to a
    separate, stronger model rather than reusing whatever the player agent
    happens to be running on."""
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch("the_village.discussion.ai_speaker.LLMGuardrail") as mock_cls:
        speaker._build_guardrail()
        _, kwargs = mock_cls.call_args
        assert kwargs["llm"].model == _GUARDRAIL_MODEL
        assert kwargs["llm"] is not speaker._player_agent.llm


def test_guardrail_does_not_log_when_llm_guardrail_passes(caplog):
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    output = SimpleNamespace(
        pydantic=_SpeakerOutput(has_something_to_say=True, text="hi")
    )
    with patch("the_village.discussion.ai_speaker.LLMGuardrail") as mock_cls:
        mock_cls.return_value = lambda o: (True, o)
        guardrail = speaker._build_guardrail()
        with caplog.at_level("WARNING"):
            passed, result = guardrail(output)
    assert passed is True
    assert caplog.text == ""


def test_speak_prompt_forbids_ungrounded_claims():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "Do NOT make any claims about what another villager did, " in prompt


def test_speak_prompt_forbids_treating_dead_players_as_active_suspects():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "out of the game" in prompt
    assert "never treat them as an active suspect" in prompt


def test_speak_prompt_allows_discussing_the_dead_players_killing():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "still fine to discuss why or how a dead player died" in prompt


def test_speak_prompt_forbids_placing_dead_players_reactions_after_their_death():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "after the day they died" in prompt


def test_speak_prompt_tells_agents_not_to_ask_already_answered_questions():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "check whether their own words in Discussion so far already answer that" in prompt


def test_speak_prompt_tells_agents_not_to_reask_an_already_explained_fact():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "a specific fact or inconsistency they've already explained" in prompt


def test_speak_prompt_tells_agents_to_avoid_repeating_heavily_discussed_topics():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "Heavily Discussed Today" in prompt
    assert "shift focus to a different player or angle" in prompt


def test_speak_prompt_encourages_checking_victims_own_voting_history():
    state = make_discussion_state()
    state.advance_day()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "own voting history" in prompt
    assert "lone or minority vote" in prompt


def test_speak_prompt_omits_voting_history_rule_on_the_first_day():
    """Night one's kill happens before any vote has ever been cast, so
    nothing exists to check -- keeping this rule active on day 1 is what
    prompted agents to invent a vote for the victim that never happened."""
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    prompt = speaker._build_speak_prompt(addressed_by=None)
    assert "own voting history" not in prompt
    assert "lone or minority vote" not in prompt


def test_speak_task_expected_output_requires_a_question_or_demand_not_just_an_accusation():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    task = speaker._build_speak_task(addressed_by=None)
    assert (
        "directly asks a question of or explicitly demands a response from"
        in task.expected_output
    )
    assert (
        "merely accuses or talks about someone without asking them anything"
        in task.expected_output
    )


async def test_returns_none_on_scheduled_decline():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(_SpeakerOutput(has_something_to_say=False))
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message is None
    assert state.current_day.discussion == []


async def test_records_decline_placeholder_when_owed_a_reply():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    asking = speaker._record_message("Where were you?", addressed_to="A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(_SpeakerOutput(has_something_to_say=False))
        ),
    ):
        message = await speaker.speak(addressed_by=asking)
    assert message.player_name == "A"
    assert message.text == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_records_message_and_resolved_address():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                _SpeakerOutput(
                    has_something_to_say=True,
                    text="I saw B leave.",
                    addressed_to="B",
                ),
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message.text == "I saw B leave."
    assert message.addressed_to == "B"
    assert state.current_day.discussion == [message]


def test_guardrail_description_forbids_turn_order_commentary():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "turn order" in description.lower()


def test_guardrail_description_distinguishes_missing_content_from_turn_order():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "not turn-order commentary" in description.lower()
    assert "request for content" in description.lower()


def test_guardrail_description_allows_recapping_an_actual_turn_order_comment():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "grounded reportage" in description.lower()


def test_guardrail_description_allows_empty_response():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "there is nothing to judge" in description.lower()


def test_guardrail_description_forbids_unfounded_behavior_claims():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "acting oddly" in description.lower()
    assert "without grounding it in something specific" in description.lower()


def test_guardrail_description_behavior_claim_rule_is_not_limited_to_a_fixed_word_list():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "not just the examples listed" in description.lower()


def test_guardrail_description_names_the_dead_player():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "B" in description
    assert "dead" in description.lower()


def test_guardrail_description_omits_dead_player_language_when_all_alive():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "dead" not in description.lower()


def test_guardrail_description_allows_grounded_analysis_of_dead_players_past_actions():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "investigative reasoning" in description.lower()


def test_guardrail_description_allows_dead_players_opinion_about_another_player():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "what they seemed to think about another player" in description.lower()


def test_guardrail_description_allows_dead_player_as_time_reference():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "time reference" in description.lower()
    assert "alibi for when" in description.lower()


def test_guardrail_description_forbids_misstated_vote_claims():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "contradicts the actual vote recorded" in description.lower()


def test_guardrail_description_allows_vague_vote_references():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "not a claim that can be checked" in description.lower()


def test_guardrail_description_forbids_a_vote_claim_with_no_recorded_vote_at_all():
    """No vote has ever been recorded on day 1, so a claim like "Hattie voted
    for Joan last night" has nothing to contradict -- the old wording only
    caught contradictions of an actual record, letting this fabrication
    through."""
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "no vote is recorded for that player" in description.lower()


def test_guardrail_description_forbids_misstated_prior_statements():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "contradicts what that player actually said" in description.lower()


def test_guardrail_description_allows_inference_about_unstated_motives():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "not a claim about their recorded words" in description.lower()


def test_guardrail_description_forbids_asking_about_an_already_reversed_belief():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "explicitly and unambiguously answered or abandoned" in description.lower()


def test_guardrail_description_forbids_reasking_an_already_explained_fact():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "account again for a specific fact or inconsistency" in description.lower()


def test_guardrail_description_allows_asking_why_they_changed_their_mind():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "not already settled by their own prior words" in description.lower()


def test_guardrail_description_allows_bare_factual_or_emotional_statement():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "flat factual or emotional statement" in description.lower()


def test_guardrail_description_forbids_dead_players_reaction_placed_after_their_death():
    state = make_discussion_state()
    dead_player = next(p for p in state.players if p.name == "B")
    dead_player.is_alive = False
    speaker = make_ai_speaker(state, "A")
    description = speaker._build_guardrail_description()
    assert "after the day they died" in description.lower()
    assert "after the lynching" in description.lower()


def test_speak_task_guardrail_is_callable():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    task = speaker._build_speak_task(addressed_by=None)
    assert callable(task.guardrail)


def test_speak_task_guardrail_max_retries_is_capped_at_one():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    task = speaker._build_speak_task(addressed_by=None)
    assert task.guardrail_max_retries == 1


async def test_returns_none_when_guardrail_keeps_failing_and_no_reply_owed():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            side_effect=Exception(
                "Task failed guardrail validation after 3 retries. Last "
                "error: Bruce is dead and out of the game."
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message is None
    assert state.current_day.discussion == []


async def test_records_decline_placeholder_when_guardrail_keeps_failing_and_reply_owed():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    asking = speaker._record_message("Where were you?", addressed_to="A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            side_effect=Exception("Task failed guardrail validation after 3 retries.")
        ),
    ):
        message = await speaker.speak(addressed_by=asking)
    assert message.player_name == "A"
    assert message.text == DECLINED_TO_RESPOND
    assert message.addressed_to is None


async def test_reraises_non_guardrail_errors():
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        try:
            await speaker.speak(addressed_by=None)
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected RuntimeError to propagate")


async def test_ignores_addressed_to_when_declining_with_nothing_to_say():
    """Even if the model somehow sets `addressed_to` alongside
    `has_something_to_say=False`, a decline must not be recorded as a
    message at all -- the field is only meaningful when there's text."""
    state = make_discussion_state()
    speaker = make_ai_speaker(state, "A")
    with patch(
        "crewai.Crew.akickoff",
        new=AsyncMock(
            return_value=_crew_result(
                _SpeakerOutput(has_something_to_say=False, addressed_to="B"),
            )
        ),
    ):
        message = await speaker.speak(addressed_by=None)
    assert message is None
