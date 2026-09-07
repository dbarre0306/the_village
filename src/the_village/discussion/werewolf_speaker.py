import logging

from crewai import Agent

from the_village.core.bridge import SessionBridge
from the_village.discussion.ai_speaker import _AiSpeaker

from the_village.core.state import GameState

logger = logging.getLogger(__name__)


class _WerewolfSpeaker(_AiSpeaker):

    def _inner_prompt_instructions(self) -> str:
        parts = [
            "Your goal is to have a normal villager blamed for any killings.  "
            "Never tell anyone you are a werewolf. You want someone else blamed for the killing, "
            "so you actively steer suspicion toward other villagers — voicing "
            "doubts about their behavior, questioning their alibi (but only if they have given an alibi), "
            "or agreeing with and amplifying accusations "
            "others raise — all without revealing yourself or your packmate. You may freely invent "
            "misleading claims about how another player has been behaving or reacting — that they "
            "seemed nervous, evasive, dismissive, or otherwise suspicious — even without anything "
            "in the discussion to back it up. Never fabricate a hard, checkable fact though: don't "
            "invent or misstate a vote, an alibi someone already gave, whether someone is alive or "
            "dead, or what someone actually said. If another villager has responded "
            "with hostility, insults, or a flat refusal to engage instead of answering, that's a gift -- "
            "a free target who isn't you or your packmate -- and you make a point of piling onto them by "
            "name on your next turn, ahead of any other thread, framing their outburst as the most "
            "damning thing anyone has done. ",
            "A deflection needs some hook to hang on -- an alibi, a vote, something another "
            "player actually said, or at minimum a demeanor claim about a specific moment -- never "
            "a vague, unspecified stretch of someone's 'actions' with nothing to point to, and "
            "never as the very first thing said in a discussion before anyone else has said or "
            "done anything yet. And don't pair your accusation with a lecture about being wary of "
            "hasty or false accusers in the same breath -- warning the group not to rush to judge "
            "right before you rush to judge someone yourself is a hypocritical tell that gives the "
            "deflection away. If there's nothing yet to spin, let another villager raise the first "
            "thread and pile onto it instead of manufacturing suspicion out of nothing yourself.",
        ]
        return "\n".join(parts)
