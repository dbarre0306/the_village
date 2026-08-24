from the_village.discussion.reply_chain import ReplyChain
from the_village.state import DiscussionMessage


class ScriptedSpeaker:
    """A fake Speaker that returns one scripted reply per call, in order."""

    def __init__(self, player_name: str, *replies: DiscussionMessage | None):
        self.player_name = player_name
        self._replies = list(replies)
        self.call_count = 0

    async def speak(self, addressed_by: DiscussionMessage | None) -> DiscussionMessage | None:
        self.call_count += 1
        return self._replies.pop(0)


def make_message(player_name: str, text: str, addressed_to: str | None = None) -> DiscussionMessage:
    return DiscussionMessage(player_name=player_name, text=text, addressed_to=addressed_to)


async def test_does_nothing_when_the_message_addresses_no_one():
    a = ScriptedSpeaker("A")
    chain = ReplyChain({"A": a})

    await chain.execute(make_message("A", "hello", addressed_to=None))

    assert a.call_count == 0


async def test_asks_the_addressed_speaker_to_reply():
    b = ScriptedSpeaker("B", make_message("B", "I was home.", addressed_to=None))
    chain = ReplyChain({"B": b})

    await chain.execute(make_message("A", "B, where were you?", addressed_to="B"))

    assert b.call_count == 1


async def test_follows_a_chain_of_replies():
    c = ScriptedSpeaker("C", make_message("C", "Nothing to add.", addressed_to=None))
    b = ScriptedSpeaker("B", make_message("B", "Ask C.", addressed_to="C"))
    chain = ReplyChain({"B": b, "C": c})

    await chain.execute(make_message("A", "B, where were you?", addressed_to="B"))

    assert b.call_count == 1
    assert c.call_count == 1


async def test_stops_when_a_reply_is_none():
    b = ScriptedSpeaker("B", None)
    chain = ReplyChain({"B": b})

    await chain.execute(make_message("A", "B, where were you?", addressed_to="B"))

    assert b.call_count == 1


async def test_a_speaker_only_replies_once_per_chain():
    """A chain that loops back to a speaker who already replied must not ask
    them again, or the chain would recurse forever."""
    b = ScriptedSpeaker("B", make_message("B", "Ask A.", addressed_to="A"))
    a = ScriptedSpeaker("A", make_message("A", "Ask B again.", addressed_to="B"))
    chain = ReplyChain({"A": a, "B": b})

    await chain.execute(make_message("A", "B, where were you?", addressed_to="B"))

    assert b.call_count == 1
    assert a.call_count == 1
