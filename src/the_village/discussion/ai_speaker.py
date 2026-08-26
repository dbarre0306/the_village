import logging
import re
from typing import Any, Final

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel, Field

from the_village.bridge import SessionBridge

from .speaker import _AddressResolution, _Speaker, DECLINED_TO_RESPOND
from the_village.state import DiscussionMessage, GameState, Player

logger = logging.getLogger(__name__)

_TURN_ORDER_COMMENTARY_PATTERN = re.compile(
    r"""
    (haven'?t|hasn'?t|has n[o']t)\s+(heard\s+from|spoken|said|talked|mentioned)
    | no\s*one\s+(has\s+)?(said|spoken|mentioned|talked)
    | (nobody|no\s+one)\s+has\s+(said|spoken|mentioned|talked)
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DEAD_PLAYER_SUSPECT_LANGUAGE_PATTERN = re.compile(
    r"""
    explain(?:s|ed|ing)? | whereabouts | \balibi\b | clarify | clarified |
    account\s+for | credibility | \bpress(?:ed|ing)?\b |
    hasn'?t\s+(?:fully\s+)?(?:explained|said|answered) |
    avoid(?:s|ed|ing)?\s+questions | evad(?:e|es|ed|ing)?\s+questions |
    evasion | suspicious\s+of |
    should\s+(?:be\s+)?voted?\s+for | vote\s+for |
    where\s+(?:\w+\s+){0,2}(?:was|were) |
    still\s+need|needs?\s+to\s+answer
    """,
    re.IGNORECASE | re.VERBOSE,
)

_UNFOUNDED_BEHAVIOR_CLAIM_PATTERN = re.compile(
    r"""
    (?:been\s+)?acting\s+(?:a\s+bit\s+|really\s+|so\s+)?
        (?:odd|strange|weird|suspicious|off|nervous|shady)
    | seem(?:s|ed)?\s+(?:a\s+bit\s+|really\s+|so\s+)?
        (?:odd|strange|weird|suspicious|off|nervous|shady|focused\s+on)
    | keeps?\s+(?:bringing\s+up|mentioning|watching|looking\s+at)
    | behaving\s+(?:oddly|strangely|suspiciously)
    | (?:her|his|their)\s+movements
    """,
    re.IGNORECASE | re.VERBOSE,
)

_DEAD_PLAYER_WINDOW: Final = 80


def _build_safe_death_explanation_pattern(name: str) -> re.Pattern[str]:
    """Matches talk about *why/how the dead player died* -- e.g. "that doesn't
    explain why Bruce was killed" -- which is legitimate murder-mystery
    discussion, not pressuring the dead player themselves. Stripped out of a
    window before the suspect-language check runs so it can't trigger a
    false-positive rejection on its own."""
    return re.compile(
        rf"""
        explain(?:s|ed|ing)?\s+(?:to\s+\w+\s+)?(?:why|how)\s+{re.escape(name)}\s+
        (?:was\s+(?:killed|murdered|slain|targeted|attacked|lynched|found\s+dead)
        | died | is\s+dead)
        """,
        re.IGNORECASE | re.VERBOSE,
    )


class _SpeakerOutput(BaseModel):
    has_something_to_say: bool = Field(
        description=(
            "Whether you have something to say right now. False means you'll "
            "sit this turn out."
        )
    )
    text: str | None = Field(
        default=None,
        description=(
            "What you say, if you have something to say. Keep it to one or "
            "two sentences -- brief, like real spoken dialogue."
        ),
    )


def _reject_turn_order_commentary(output: Any) -> tuple[bool, Any]:
    speaker_output: _SpeakerOutput | None = output.pydantic
    text = speaker_output.text if speaker_output else None
    if text and _TURN_ORDER_COMMENTARY_PATTERN.search(text):
        return (
            False,
            "Your message comments on who has or hasn't spoken yet. Turn "
            "order is random and not evidence of anything -- remove that "
            "commentary and say only things grounded in Known facts or "
            "what was actually said.",
        )
    return (True, output)


def _reject_unfounded_behavior_claims(output: Any) -> tuple[bool, Any]:
    speaker_output: _SpeakerOutput | None = output.pydantic
    text = speaker_output.text if speaker_output else None
    if text and _UNFOUNDED_BEHAVIOR_CLAIM_PATTERN.search(text):
        return (
            False,
            "Your message makes a vague, unfounded claim about how another "
            "player has been behaving (e.g. acting odd, seeming suspicious, "
            "their movements). Only describe behavior that the discussion "
            "above actually shows -- remove the vague claim or ground it in "
            "something specific that was actually said.",
        )
    return (True, output)


def _build_dead_player_guardrail(dead_names: list[str]):
    safe_death_explanation_patterns = {
        name: _build_safe_death_explanation_pattern(name) for name in dead_names
    }

    def _reject_dead_player_as_active_suspect(output: Any) -> tuple[bool, Any]:
        speaker_output: _SpeakerOutput | None = output.pydantic
        text = speaker_output.text if speaker_output else None
        if not text:
            return (True, output)
        for name in dead_names:
            for match in re.finditer(rf"\b{re.escape(name)}\b", text):
                window_start = max(0, match.start() - _DEAD_PLAYER_WINDOW)
                window_end = min(len(text), match.end() + _DEAD_PLAYER_WINDOW)
                window = text[window_start:window_end]
                window = safe_death_explanation_patterns[name].sub("", window)
                if _DEAD_PLAYER_SUSPECT_LANGUAGE_PATTERN.search(window):
                    return (
                        False,
                        f"{name} is dead and out of the game -- don't talk "
                        "about them as a suspect who still needs to explain "
                        "themselves, clarify their whereabouts, or answer "
                        "questions. Remove that and only discuss currently "
                        "living players as suspects.",
                    )
        return (True, output)

    return _reject_dead_player_as_active_suspect


class _AiSpeaker(_Speaker):

    def __init__(
        self,
        state: GameState,
        bridge: SessionBridge,
        player_name: str,
        player_agent: Agent,
        analyst_agent: Agent,
    ):
        super().__init__(state, bridge, player_name, analyst_agent)
        self._player_agent = player_agent

    async def _speak(
        self,
        addressed_by: DiscussionMessage | None,
    ) -> DiscussionMessage | None:

        speak_task = self._build_speak_task(addressed_by)
        analyze_task = self._build_analyze_task(speak_task)
        crew = self._build_crew(speak_task, analyze_task)

        try:
            result = await crew.akickoff()
        except Exception as exc:
            if "guardrail" not in str(exc).lower():
                raise
            logger.warning(
                "%s's turn kept failing guardrail validation and was skipped: %s",
                self._player_name,
                exc,
            )
            if addressed_by is None:
                return None
            return self._record_message(DECLINED_TO_RESPOND, addressed_to=None)

        speakerOutput = result.tasks_output[0].pydantic or _SpeakerOutput(
            has_something_to_say=False
        )

        if not speakerOutput.has_something_to_say or not speakerOutput.text:
            if addressed_by is None:
                return None
            return self._record_message(DECLINED_TO_RESPOND, addressed_to=None)

        resolution = result.tasks_output[1].pydantic or _AddressResolution(
            addressed_to=None
        )
        addressed_to = self._resolve_target(resolution.addressed_to)
        return self._record_message(speakerOutput.text, addressed_to)

    def _build_speak_task(self, addressed_by: str | None) -> Task:
        return Task(
            description=self._build_speak_prompt(addressed_by),
            agent=self._player_agent,
            expected_output="A SpeakerOutput saying whether you have something to say.",
            output_pydantic=_SpeakerOutput,
            guardrail=self._build_guardrail(),
        )

    def _build_guardrail(self):
        reject_dead_player_as_active_suspect = _build_dead_player_guardrail(
            self._state.names_of_dead_players()
        )

        def _guardrail(output: Any) -> tuple[bool, Any]:
            passed, result = _reject_turn_order_commentary(output)
            if passed:
                passed, result = _reject_unfounded_behavior_claims(output)
            if passed:
                passed, result = reject_dead_player_as_active_suspect(output)
            if not passed:
                speaker_output: _SpeakerOutput | None = output.pydantic
                failed_text = speaker_output.text if speaker_output else None
                logger.warning(
                    "%s's speaker output failed guardrail validation: "
                    "reason=%s text=%r",
                    self._player_name,
                    result,
                    failed_text,
                )
            return passed, result

        return _guardrail

    def _build_speak_prompt(self, addressed_by: DiscussionMessage | None) -> str:
        parts = [
            "Known facts:",
            self._state.format_current_day(),
            self._state.format_deaths(),
            self._state.format_lynchings(),
            "",
            f"Other living players: {', '.join(self._names_of_other_living_players)}",
            "",
            "Discussion so far:",
            self._state.format_history(),
            "",
            f"Today's discussion should focus on {self._state.current_day.player_found_dead}'s "
            "killing, since that's what just happened -- but don't ignore the earlier "
            "killings and lynchings listed in Known facts above; bring them up when "
            "they're relevant.",
            "",
            "Only treat something as true if it's listed in Known facts above or was "
            "actually said in Discussion so far -- never invent a sighting, alibi, or "
            "claim about what another villager did or how they've been behaving. For "
            'example, never say someone "seems focused on" or "keeps bringing up" '
            'another player, and never say someone has "been acting odd/strange/'
            'suspicious" or bring up their "movements" unless the discussion above '
            "actually shows them doing that. Turn order is random and says nothing "
            "about anyone's guilt or honesty, so never comment on who has or hasn't "
            "spoken yet, or how much "
            "someone has said. This also applies to the group as a whole -- never "
            'claim something like "no one seems to remember where they were" or '
            '"it\'s strange no one has said X" unless the discussion above actually '
            "shows people being asked and failing to answer. If no one has addressed "
            "a topic yet, that just means it hasn't come up -- it is not evidence of "
            "anything. Players listed above as killed or lynched are dead and out of "
            "the game -- never treat them as suspects who still need to explain "
            "themselves or clarify their whereabouts, never press them for answers, "
            "never say they are evading, dodging, or avoiding questions, never accuse "
            "them of anything, never talk as if they might still speak or be voted "
            "on, and never cite their earlier claims alongside a living player's as "
            "if their story is still being compared or their credibility is still in "
            "question -- their part in the game is over, so leave them out of "
            "arguments about who is currently suspicious entirely. It's still fine "
            "to discuss why or how a dead player died, and to ask living players "
            "about their own whereabouts or actions -- that's discussing the "
            "murder, not pressuring the dead player themselves.",
            "",
            "When referring to another player, always use their name -- never a pronoun.",
            "When referring to more than one player, always use all of their names -- never a pronoun.",
            "",
            "Any statements, questions, or accusations must be consistent with what you previously said.",
            "",
        ]

        if addressed_by is not None:
            parts.append(
                f'{addressed_by.player_name} just said to you: "{addressed_by.text}" '
                "Respond directly to this. You can opt to say nothing if it is in your "
                "best interest.",
            )
        else:
            parts.append(
                "It's your turn. Decide whether you have something to say -- a "
                "statement, question, or accusation. If you have nothing to add, "
                "say so."
            )
        return "\n".join(parts)

    def _build_analyze_task(self, speak_task: Task) -> Task:
        return Task(
            description=self._build_analyze_prompt(),
            agent=self._analyst_agent,
            expected_output="An AddressResolution naming who, if anyone, was addressed.",
            output_pydantic=_AddressResolution,
            context=[speak_task],
        )

    def _build_crew(self, speak_task: Task, analyze_task: Task) -> Crew:
        return Crew(
            agents=[self._player_agent, self._analyst_agent],
            tasks=[speak_task, analyze_task],
            process=Process.sequential,
        )

    def _build_analyze_prompt(self) -> str:
        parts = [
            f"Names of other players: {', '.join(self._names_of_other_living_players)}.",
            "",
            "Determine which of the other players, if anyone, the message you were just given as ",
            "context is directed at. If the speaker had nothing to say, there ",
            "is nothing to analyze -- leave addressed_to unset.",
        ]
        return "\n".join(parts)
