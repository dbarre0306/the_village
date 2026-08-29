from abc import abstractmethod
import logging
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.tasks.llm_guardrail import LLMGuardrail
from pydantic import BaseModel, Field

from the_village.bridge import SessionBridge

from .speaker import _AddressResolution, _Speaker, DECLINED_TO_RESPOND
from the_village.state import DiscussionMessage, GameState, Player

logger = logging.getLogger(__name__)


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
        """Wraps CrewAI's own LLMGuardrail rather than passing a plain string,
        so each attempt's candidate text and rejection reason can be logged --
        the string-guardrail path CrewAI runs internally exposes neither
        through its events, which made a real production miss impossible to
        diagnose from logs alone."""
        llm_guardrail = LLMGuardrail(
            description=self._build_guardrail_description(),
            llm=self._player_agent.llm,
        )
        attempt = 0

        def _guardrail(output: Any) -> tuple[bool, Any]:
            nonlocal attempt
            passed, result = llm_guardrail(output)
            if not passed:
                speaker_output: _SpeakerOutput | None = output.pydantic
                text = speaker_output.text if speaker_output else None
                logger.warning(
                    "%s's speaker output failed the LLM guardrail (attempt "
                    "%s): reason=%s text=%r",
                    self._player_name,
                    attempt,
                    result,
                    text,
                )
            attempt += 1
            return passed, result

        return _guardrail

    def _build_guardrail_description(self) -> str:
        parts = [
            "The task result is a JSON object with a `text` field containing "
            "what the player said aloud. Judge only the content of that field "
            "against the rules below.",
            "If `has_something_to_say` is false, or the `text` field is "
            "null or empty, that is always valid -- there is nothing to "
            "judge.",
            "Be conservative: reject only if the text explicitly and "
            "directly violates a rule. Do not infer, assume, or read "
            "between the lines -- if a violation would require guessing at "
            "unstated intent, or the text is a generic question or request "
            "addressed to the whole group (e.g. asking what everyone did, "
            "or asking living players to share their whereabouts), it is "
            "valid. When you do reject, quote the exact phrase that "
            "violates the rule.",
            "Reject only if the text explicitly comments on turn order -- "
            "i.e. whether someone has spoken or been heard from at all "
            '(e.g. "you haven\'t said anything," "no one has spoken yet," '
            '"we haven\'t heard from you"). A statement that players '
            "haven't provided some specific piece of information (e.g. an "
            "alibi, an answer, evidence) is not turn-order commentary, "
            'even when phrased as "none of you have..." or "no one has '
            "provided...\" -- that's a request for content, not a comment "
            "on participation.",
            "Recapping a turn-order comment that a specific player "
            'actually made earlier in Discussion so far (e.g. "Stephen '
            "questioned Della's silence earlier\") is grounded reportage "
            "of what was said, not a fresh turn-order complaint, and is "
            "valid.",
            "Reject only if the text explicitly asserts, about a specific "
            "named player, that they have been acting oddly/strangely/"
            "suspiciously/nervously/shady, or references their movements, "
            "without grounding it in something specific. A generic "
            "question or request directed at the group is not a behavior "
            "claim about anyone.",
        ]
        dead_names = self._state.dead_players_names()
        if dead_names:
            names = ", ".join(dead_names)
            first_name = dead_names[0]
            verb = "is" if len(dead_names) == 1 else "are"
            parts.append(
                f"{names} {verb} dead and out of the game. The key test: "
                "forbid only text that treats them as someone who could "
                "still respond or act right now -- pressing them for new "
                "whereabouts, alibi, or explanations; claiming they are "
                "currently evading questions; comparing their ongoing "
                "credibility or story to a living player's; or suggesting "
                "they might still speak or be voted on. Generic statements "
                'addressed to "everyone" or the group do not count as '
                "naming them, since the living players obviously "
                "understand that to mean the living players."
            )
            parts.append(
                "Everything else about a dead player is valid: discussing "
                "why or how they died; asking a living player about their "
                "own whereabouts or actions, even when the dead player's "
                "name or death is used only as a time reference (e.g. "
                f'"where were you when {first_name} was killed", "do you '
                f'have an alibi for when {first_name} died"); a flat '
                "factual or emotional statement about them on its own "
                f'(e.g. "{first_name} was killed", "this is terrible news '
                f'about {first_name}"); and analyzing their own past '
                "actions, statements, or apparent beliefs from while they "
                "were alive -- including what they seemed to think about "
                "another player, living or dead -- as investigative "
                f'reasoning about what already happened (e.g. "Don was '
                'quick to accuse Martha -- maybe he was deflecting", "Don '
                'seemed to think Martha was acting suspicious"), as long as '
                "it's grounded in Discussion so far or Known facts."
            )
        return "\n\n".join(parts)

    def _build_speak_prompt(self, addressed_by: DiscussionMessage | None) -> str:
        parts = [
            self._state.known_facts(self._player_name),
            "",
            "# Instructions",
            self._prompt_instructions(),
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

    def _prompt_instructions(self) -> str:
        parts = [
            f"Today's discussion should focus on {self._state.current_day.player_found_dead}'s "
            "killing, since that's what just happened -- but don't ignore any earlier "
            "killings and lynchings listed in Known Facts above; bring them up when "
            "they're relevant.",
            "",
            "Only treat something as true if it's listed in Known Facts above or was "
            "actually said in this discussion or previous discussions. ",
            "",
            "You may make things up about yourself -- for example, inventing an alibi. You must "
            "remain consistent throughout all of the discussions.  Do NOT say contradictory things. ",
            "",
            "Anything you say about someone else must be grounded in what you actually know or "
            "what has already been said.  Do NOT make any claims about what another villager did, "
            "or said. ",
            "",
            self._inner_prompt_instructions(),
            "",
            "You speak the way people actually do in a tense group conversation: briefly. "
            "One or two sentences, never a speech. Speak in first person as yourself -- "
            "never refer to yourself by name or in the third person. "
            "",
            "Players listed above as killed or lynched are dead and out of the "
            "game -- never treat them as an active suspect (pressing them for "
            "answers, comparing their story to a living player's, accusing "
            "them, and so on). It's still fine to discuss why or how a dead "
            "player died, and to ask living players about their own "
            "whereabouts or actions.",
            "",
            "When referring to another player, always use their name -- never a pronoun.",
            "",
            "Any statements, questions, or accusations must be consistent with what you previously said.",
            "",
        ]
        return "\n".join(parts)

    @abstractmethod
    def _inner_prompt_instructions(self) -> str:
        pass

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
            "context is directed at. If the message is to everyone, then there is no specific player ",
            "being addressed - leave addressed_to unset.  If the message is addressed to multiple ",
            "players, then leave addressed_to to unset. If the speaker had nothing to say, there ",
            "is nothing to analyze -- leave addressed_to unset.",
        ]
        return "\n".join(parts)
