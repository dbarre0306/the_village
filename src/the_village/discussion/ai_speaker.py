from abc import abstractmethod
import logging
import os
from typing import Any, Callable, NamedTuple

from crewai import LLM, Agent, Crew, Process, Task
from crewai.tasks.llm_guardrail import LLMGuardrail
from pydantic import BaseModel, Field

from the_village.core.bridge import SessionBridge

from .speaker import _Speaker, DECLINED_TO_RESPOND
from the_village.core.state import DiscussionMessage, GameState, Player

logger = logging.getLogger(__name__)

_DEAD_AND_OUT_OF_GAME = "dead and out of the game"

# Villagers have no reason to lie, so an ungrounded read on someone's demeanor
# is treated as a hallucination and rejected. Werewolves need exactly that
# latitude to deflect suspicion, so this text is only appended to the
# guardrail for non-werewolf speakers -- see _build_guardrail_description.
_DEMEANOR_GROUNDING_GUARDRAIL_TEXT = (
    "Reject only if the text explicitly asserts, about a specific named "
    "player, that they have been acting oddly/strangely/suspiciously/"
    "nervously/shady/defensively/evasively, or displaying any other "
    "suspicious demeanor or reaction, or references their movements, "
    "without grounding it in something specific. This covers any wording "
    "with that same meaning, not just the examples listed. A generic "
    "question or request directed at the group is not a behavior claim "
    "about anyone."
)

# The guardrail judges speaker output against a multi-clause conditional
# prompt ("reject only if explicit", "don't infer"). The player's own model
# (MODEL, often a small/cheap one) proved unreliable at following those
# conditions faithfully -- it kept rejecting generic, ungrounded statements
# on invented implications despite repeated prompt tightening. Judging needs
# a stronger model than generating does, so the guardrail gets its own.
_GUARDRAIL_MODEL = os.environ.get("GUARDRAIL_MODEL", "gpt-5-mini")


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
    addressed_to: str | None = Field(
        default=None,
        description=(
            "The name of the living villager your text directly asks a "
            "question of, or explicitly demands a response from, if any. "
            "Merely accusing or talking about a player -- without asking "
            "them anything or demanding they respond -- doesn't count. "
            "Leave unset if the message isn't addressing anyone in "
            "particular, is addressed to the whole group or to multiple "
            "players, or if you have nothing to say."
        ),
    )


class _ConditionalRule(NamedTuple):
    """A single behavioral constraint that's both taught to the speaker
    (prompt_text, a proactive instruction) and enforced against its output
    (guardrail_text, a rejection criterion). Keeping the two phrasings next
    to each other, behind one `applies` check, is what stops a future fix
    from patching one side and forgetting the other."""

    applies: Callable[[GameState, str], bool]
    prompt_text: str
    guardrail_text: str


_ALREADY_ANSWERED_QUESTION_RULE = _ConditionalRule(
    applies=lambda state, player_name: True,
    prompt_text=(
        "Before asking a player anything -- a question, a challenge, a "
        "rhetorical jab, a request to justify or reconsider -- about a "
        "specific fact, alibi, whereabouts, belief, or suspicion of theirs, "
        "do this check first: re-read their own messages in Discussion so "
        "far and see if they already stated that fact plainly. If they did, "
        "do not raise it again in ANY form, including ones that don't look "
        "like a repeat question, such as: mocking or expressing disbelief "
        'at the answer they gave (e.g. "do you really think sleeping '
        'through it is a solid excuse?"), asking them to justify or '
        "reconsider it, or asking them to explain or account for it again. "
        "All of those are the same forbidden move wearing different words. "
        "Instead, respond to what they actually said: agree with it, "
        "explain specifically why you don't buy it, or move on to a "
        "different player or angle entirely."
    ),
    guardrail_text=(
        "Reject only if the text raises a specific fact, alibi, "
        "whereabouts, belief, or suspicion of a named player's that "
        "they have already explicitly and unambiguously stated or "
        "answered earlier in Discussion so far, in ANY form -- a "
        "direct re-ask, a request to justify/reconsider/explain it "
        "again, or a rhetorical challenge or expression of disbelief "
        "aimed at the answer already given (e.g. Martha already said "
        '"I was at home, just trying to get some sleep too," then '
        'being asked "do you really think that sleeping through all '
        'the noise is a solid excuse for not seeing anything?"; or '
        'asking Don "do you still think Bruce was the werewolf?" '
        "right after Don said he was wrong to suspect Bruce). These "
        "are the same forbidden move regardless of phrasing -- judge "
        "by whether the player's own prior words already settle the "
        "point being raised, not by whether the sentence is phrased "
        "as a question. Quote or closely paraphrase the player's own "
        "prior statement to check this -- only reject when it already "
        "and directly settles the point, not when the prior statement "
        "was hedged or ambiguous. A question about why they changed "
        "their mind, what they think now, or anything else not "
        "already settled by their own prior words, is valid."
    ),
)

_PRE_ANNOUNCEMENT_KNOWLEDGE_RULE = _ConditionalRule(
    applies=lambda state, player_name: bool(state.dead_players_names())
    and not state.is_werewolf(player_name),
    prompt_text=(
        "You only learn that someone was killed when their body is found the "
        "next morning, as recorded in Known Facts above -- you have no knowledge "
        "of a death before it's discovered. Never claim to have heard, suspected, "
        "or known about a killing before it was found (for example, hearing the "
        'news "last night," or before the morning it was announced). This also '
        "applies to your alibi for that night -- don't describe reacting to, "
        "mourning, or processing the killing as something you were doing "
        "overnight, and don't explain a precaution you took that night -- "
        "locking your doors, staying inside, and the like -- as motivated by "
        'fear or worry about that specific killing (e.g. "I was worried after '
        'what happened" or "I locked myself in out of fear after what happened '
        'to [victim]"). That killing could only be known, and reacted or '
        "responded to, after the body was found the next morning; any "
        "overnight caution you describe must be generic, not attributed to a "
        "killing you had no way of knowing about yet."
    ),
    guardrail_text=(
        "Reject only if the text explicitly claims the speaker personally "
        "knew, heard, suspected, or otherwise learned that a specific "
        "killing had happened before it was found and announced in Known "
        'Facts above (e.g. claiming to have heard the news "last night," '
        "or before the morning the body was discovered). This includes "
        "describing an alibi for the night of the killing as time spent "
        'reacting to, mourning, or "processing" that killing (e.g. '
        '"gathering my thoughts about the murder" as an account of what '
        "the speaker was doing that night), and it includes explaining an "
        "overnight precaution -- locking doors, staying inside, and the "
        "like -- as motivated by fear, worry, or unease about that "
        'specific killing (e.g. "I was worried after what happened," or '
        '"I locked myself in out of fear after what happened to Joan") '
        "-- the reaction itself is a pre-discovery timing claim, even "
        'without the words "last night" and even when phrased as fear '
        "or worry rather than explicit knowledge. A statement that only "
        "refers to a death after it was found, or a generic reaction to "
        "the news given as a present-tense response in today's "
        "discussion, is valid."
    ),
)

_NO_PRIOR_WEREWOLF_FEAR_RULE = _ConditionalRule(
    applies=lambda state, player_name: state.day_number == 1
    and not state.is_werewolf(player_name),
    prompt_text=(
        "Last night was the first night the village has ever had -- "
        "nothing had happened before it. You had no reason yet to fear, "
        "suspect, or take precautions against werewolves, so never claim "
        "you already had a habit or established pattern of doing so (for "
        'example, locking your doors "every night" or "because of the '
        'werewolves" as if that had long been your routine). It is still '
        "fine to say you only started doing that last night, out of "
        "ordinary caution or unease, without any prior pattern -- but that "
        "unease can't be attributed to the killing itself (see rule 13), "
        "since you had no way of knowing about it yet."
    ),
    guardrail_text=(
        "Last night was the first night the village has ever had -- "
        "before that first night, nothing had happened yet. Reject only "
        "if the text explicitly claims the speaker already had a habit "
        "or established pattern of fearing, suspecting, or taking "
        'precautions against werewolves predating last night (e.g. "I '
        'always lock my doors because of the werewolves," or "ever '
        "since the killings started\" when only last night's death has "
        "happened). A statement that only describes what the speaker "
        "did or started doing last night itself, without claiming it "
        "was already an established habit or attributing it to that "
        "specific killing, is valid."
    ),
)


_NO_BLAME_FOR_UNSPOKEN_PLAYERS_RULE = _ConditionalRule(
    applies=lambda state, player_name: True,
    prompt_text=(
        "Before saying a named player hasn't shared, given, or provided "
        "some piece of information -- an alibi, their whereabouts, or "
        "anything else -- check today's Discussion in Known Facts above "
        "for whether they've spoken at all yet today. If they have zero "
        "messages there today, they simply haven't had a turn yet; that's "
        "not evidence of anything, so don't frame it as suspicious, call "
        "them out for it, or urge the group to focus on them for that "
        "reason. A player who has spoken at least once today but didn't "
        "share the specific information in question is fair game to call "
        "out."
    ),
    guardrail_text=(
        "Reject only if the text singles out a specific named living "
        "player as not having shared, given, or provided some piece of "
        "information -- an alibi, their whereabouts, or anything else -- "
        "when today's Discussion in Known Facts above shows zero messages "
        "from that player so far today (e.g. \"Don and Bruce haven't "
        'given us much information yet" when neither Don nor Bruce has '
        "spoken today). That player simply hasn't had a turn yet, so "
        "framing their lack of a turn as if it reflects something about "
        "them -- evasiveness, unwillingness to cooperate, or the like -- "
        "is baseless. A player who has spoken at least once today but "
        "didn't share the specific information being asked about is a "
        "valid target for this complaint."
    ),
)


_NO_ADOPTING_UNVERIFIED_ACCUSATIONS_RULE = _ConditionalRule(
    applies=lambda state, player_name: not state.is_werewolf(player_name),
    prompt_text=(
        "When another player claims a third player is acting calm, nervous, "
        "evasive, defensive, or otherwise suspicious, that claim is only that "
        "player's own unverified opinion -- not a fact, and not something you "
        "may treat as confirmed just because it was said. If asked to weigh "
        "in, don't co-sign it as true (e.g. \"I share your concern, their "
        'calmness does seem unusual") unless you have your own independent '
        "reason -- grounded in Known Facts above or something the target "
        "actually said or did -- to think so. Instead, you can note that they "
        "raised it, ask what makes them think that, push back on it, or offer "
        "a separate observation of your own that's actually grounded."
    ),
    guardrail_text=(
        "Reject only if the text affirms, restates as true, or otherwise "
        "agrees with another player's demeanor or suspicion claim about a "
        'third named player (e.g. echoing that someone "does seem" calm, '
        "nervous, evasive, or suspicious right after another player raised "
        "it) without the speaker giving their own independent grounding -- "
        "from Known Facts above or something the target actually said or "
        "did -- beyond the fact that the other player said so. Merely "
        "reporting that another player made the claim, asking them what "
        "makes them think that, pushing back on it, or offering a separate "
        "independently-grounded observation is valid."
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
        crew = self._build_crew(speak_task)

        try:
            # kickoff_async (not akickoff): this crew's guardrail is a real
            # LLMGuardrail making its own blocking LLM call synchronously
            # (crewai invokes guardrails as a plain, un-awaited `def` even on
            # the akickoff() path) -- on akickoff() that call freezes the
            # whole shared event loop, stalling every other concurrent
            # session for its duration. kickoff_async() runs the entire
            # kickoff (agent turn + guardrail) via asyncio.to_thread, which
            # actually keeps the loop free for other sessions.
            result = await crew.kickoff_async()
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

        addressed_to = self._resolve_target(speakerOutput.addressed_to)
        return self._record_message(speakerOutput.text, addressed_to)

    def _build_speak_task(self, addressed_by: str | None) -> Task:
        return Task(
            description=self._build_speak_prompt(addressed_by),
            agent=self._player_agent,
            expected_output=(
                "A JSON object with `has_something_to_say` (bool); if true, "
                "`text`: one or two sentences of first-person spoken dialogue "
                "that stay consistent with everything you've said before and "
                "make no unfounded claims about other players; and "
                "`addressed_to`: the name of the single living player your "
                "text directly asks a question of or explicitly demands a "
                "response from, if any -- left unset when addressed to the "
                "whole group, addressed to multiple players, merely accuses "
                "or talks about someone without asking them anything, or "
                "when you have nothing to say."
            ),
            output_pydantic=_SpeakerOutput,
            guardrail=self._build_guardrail(),
            guardrail_max_retries=1,
        )

    def _build_guardrail(self):
        """Wraps CrewAI's own LLMGuardrail rather than passing a plain string,
        so each attempt's candidate text and rejection reason can be logged --
        the string-guardrail path CrewAI runs internally exposes neither
        through its events, which made a real production miss impossible to
        diagnose from logs alone."""
        llm_guardrail = LLMGuardrail(
            description=self._build_guardrail_description(),
            llm=LLM(model=_GUARDRAIL_MODEL),
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
            self._state.known_facts(self._player_name),
            "",
            "The rules below reference the Known Facts and Daily History above "
            "-- use them to check whether a claim is actually grounded, rather "
            "than taking the speaker's wording at face value.",
            "",
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
            "Reject only if the text explicitly claims a specific player "
            "voted for, or abstained from voting for, a specific lynch "
            "target, and either no vote is recorded for that player at all "
            "in Known Facts above, or the claim contradicts the actual vote "
            'recorded for them (e.g. "you voted for Henry yesterday" when '
            "Known Facts records that player voting for Alice, or "
            '"Hattie voted for Joan last night" when the game has had no '
            "vote at all yet). A vague or unattributed reference to a past "
            'vote (e.g. "remember who you voted for") is not a claim that '
            "can be checked, and is valid.",
            "Reject only if the text states, as fact, a specific position, "
            "priority, or preference for a named player that directly "
            "contradicts what that player actually said earlier in "
            'Discussion so far (e.g. asserting a player "wants to mourn '
            'instead of investigating" or "would rather move on" when that '
            "player's own recorded words said the opposite). Quote or "
            "closely paraphrase the player's own prior statement to check "
            "this -- only reject when it's a direct contradiction, not "
            "merely an uncharitable reading. An opinion about a player's "
            "unstated motive, intent, or hidden agenda (e.g. accusing them "
            "of using a true statement as a distraction) is inference, not "
            "a claim about their recorded words, and is valid.",
            _ALREADY_ANSWERED_QUESTION_RULE.guardrail_text,
            _NO_BLAME_FOR_UNSPOKEN_PLAYERS_RULE.guardrail_text,
        ]
        dead_names = self._state.dead_players_names()
        if dead_names:
            names = ", ".join(dead_names)
            first_name = dead_names[0]
            verb = "is" if len(dead_names) == 1 else "are"
            parts.append(
                f"{names} {verb} {_DEAD_AND_OUT_OF_GAME}. The key test: "
                "forbid only text that treats them as someone who could "
                "still respond or act right now -- pressing them for new "
                "whereabouts, alibi, or explanations; claiming they are "
                "currently evading questions; comparing their ongoing "
                "credibility or story to a living player's; or suggesting "
                "they might still speak or be voted on; or naming them as a "
                "current suspect or possible werewolf going forward. "
                'Generic statements addressed to "everyone" or the group do '
                "not count as naming them, since the living players "
                "obviously understand that to mean the living players."
            )
            parts.append(
                "Reject also if the text places one of them reacting, "
                "feeling, or behaving at a point in time after the day "
                "they died -- for example, describing their defensiveness "
                '"after the lynching," claiming they now "feel cornered," '
                'or saying they "just lost" someone who died after they '
                "themselves did. A dead player's demeanor or reaction can "
                "only be described as something from before they died."
            )
            parts.append(
                "Reject also if the text attributes responsibility or "
                "involvement for anything that happened after a dead "
                "player's own death to them -- for example, tying a later "
                'killing to their "involvement," suggesting they were '
                '"behind" a death that came after theirs, or implying '
                "they are still targeting or acting against players who "
                "question them. A dead player cannot cause, contribute "
                "to, or participate in any event -- a killing, a vote, "
                "or anything else -- that happened after the day they "
                "died; a later death can only be discussed as something "
                "someone else, still alive, is responsible for."
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
        if not self._state.is_werewolf(self._player_name):
            parts.append(_DEMEANOR_GROUNDING_GUARDRAIL_TEXT)
        for rule in (
            _PRE_ANNOUNCEMENT_KNOWLEDGE_RULE,
            _NO_PRIOR_WEREWOLF_FEAR_RULE,
            _NO_ADOPTING_UNVERIFIED_ACCUSATIONS_RULE,
        ):
            if rule.applies(self._state, self._player_name):
                parts.append(rule.guardrail_text)
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
            "discussions, killings, lynchings, or votes listed in Known Facts above; bring them up when "
            "they're relevant.",
            "",
            "## Rules",
            "",
            "1. Only treat something as true if it's listed in Known Facts above or was "
            "actually said in this discussion or previous discussions.",
            "",
            "2. You may make things up about yourself -- for example, inventing an alibi. You must "
            "remain consistent throughout all of the discussions.  Do NOT say contradictory things.",
            "",
            f"3. {self._grounding_rule_text()}",
            "",
            f"4. {self._inner_prompt_instructions().strip()}",
            "",
            "5. You speak the way people actually do in a tense group conversation: briefly. "
            "One or two sentences, never a speech. Speak in first person as yourself -- "
            "never refer to yourself by name or in the third person.",
            "",
            f"6. Players listed above as killed or lynched are {_DEAD_AND_OUT_OF_GAME} -- "
            "never treat them as an active suspect (pressing them for "
            "answers, comparing their story to a living player's, accusing "
            "them, and so on), and never describe their reactions, "
            "feelings, or behavior as happening after the day they died -- "
            "for example, a dead player can't be \"defensive after the "
            'lynching" or "feel cornered" over something that happened '
            "after their own death. It's still fine to discuss why or how a "
            "dead player died, and to ask living players about their own "
            "whereabouts or actions.",
            "",
            "7. A dead player cannot be a suspect. Once someone is dead, they're "
            "removed from play for good, whether they were a werewolf or not -- "
            "don't name them as a current suspect, float them as the possible "
            "werewolf, or urge the group to keep an eye on them going forward. "
            "It's still fine to analyze what a dead player did or seemed to "
            "believe while they were alive as reasoning about who's guilty now. "
            "A dead player also cannot be responsible for anything that "
            "happens after their own death -- don't tie a later killing or "
            'any other event to their "involvement," suggest they were '
            '"behind" it, or imply they\'re still targeting players who '
            "question them; a death that happens after someone died must be "
            "attributed to a player who's still alive.",
            "",
            "8. When referring to another player, always use their name -- never a pronoun.",
            "",
            "9. Any statements, questions, or accusations must be consistent with what you previously said.",
            "",
            f"10. {_ALREADY_ANSWERED_QUESTION_RULE.prompt_text}",
            "",
            '11. Check the "Heavily Discussed Today" list in Known Facts above. If a '
            "player listed there keeps coming up without new information, don't just "
            "restate a question or accusation about them -- either add something "
            "genuinely new, or shift focus to a different player or angle.",
            "",
            f"12. {_NO_BLAME_FOR_UNSPOKEN_PLAYERS_RULE.prompt_text}",
            "",
            "",
        ]
        if self._state.day_number > 1:
            # Night one's kill happens before any vote has ever been cast, so
            # there is no voting history yet to check -- keeping this rule
            # unconditional prompted agents to invent one for the victim.
            parts.append(
                "13. When someone new turns up dead, check their own voting history in "
                "the Daily History in Known Facts above. If they cast a vote to lynch "
                "someone who's still alive, that's worth raising as a possible reason "
                "the werewolves targeted them -- retaliation against an accuser is a "
                "classic signal that the accused is the werewolf."
            )
            parts.append("")
        if not self._state.is_werewolf(self._player_name):
            parts.append(f"14. {_PRE_ANNOUNCEMENT_KNOWLEDGE_RULE.prompt_text}")
            parts.append("")
            parts.append(
                "15. When inventing your own alibi, never base it on what another player "
                "already claimed (for example, saying you were with someone just because "
                "they already claimed it). Your alibi must be your own invention, not a "
                "copy of someone else's."
            )
            parts.append("")
            parts.append(
                "16. Being home alone with no one to vouch for them is not suspicious "
                "on its own -- most people are alone at night. Don't treat another "
                "player's alibi as suspicious just because no one can confirm it; only "
                "raise suspicion about an alibi if it's inconsistent, contradicted by "
                "other evidence, or the player is being evasive when asked about it."
            )
            parts.append("")
            if _NO_PRIOR_WEREWOLF_FEAR_RULE.applies(self._state, self._player_name):
                parts.append(f"17. {_NO_PRIOR_WEREWOLF_FEAR_RULE.prompt_text}")
                parts.append("")
            parts.append(f"18. {_NO_ADOPTING_UNVERIFIED_ACCUSATIONS_RULE.prompt_text}")
            parts.append("")
        return "\n".join(parts)

    def _grounding_rule_text(self) -> str:
        if self._state.is_werewolf(self._player_name):
            return (
                "Anything you say about someone else's words, votes, alibi, "
                "or life-or-death status must be grounded in what you "
                "actually know or what has already been said -- never "
                "invent or misstate one of those. Behavior and demeanor are "
                "fair game to spin: you may claim another player seemed "
                "nervous, evasive, dismissive, or otherwise suspicious even "
                "without solid grounding, to deflect suspicion."
            )
        return (
            "Anything you say about someone else must be grounded in what "
            "you actually know or what has already been said.  Do NOT make "
            "any claims about what another villager did, or said."
        )

    @abstractmethod
    def _inner_prompt_instructions(self) -> str:
        pass

    def _build_crew(self, speak_task: Task) -> Crew:
        return Crew(
            agents=[self._player_agent],
            tasks=[speak_task],
            process=Process.sequential,
        )
