from the_village.state import WEEKDAYS, Death, DiscussionMessage, GameState, Lynching, VoteRecord, Villager


def test_villager_defaults():
    villager = Villager(name="Alice", player_type="villager")
    assert villager.is_pack_leader is False
    assert villager.is_alive is True


def test_death_fields():
    death = Death(name="Alice", day_number=2)
    assert death.name == "Alice"
    assert death.day_number == 2


def test_game_state_defaults():
    state = GameState()
    assert state.player_name == ""
    assert state.day_number == 1
    assert state.villagers == []
    assert state.deaths == []


def test_weekdays_starts_on_sunday():
    assert WEEKDAYS[0] == "Sunday"
    assert len(WEEKDAYS) == 7


def test_discussion_message_fields():
    message = DiscussionMessage(day_number=1, speaker="Alice", message="hello")
    assert message.day_number == 1
    assert message.speaker == "Alice"
    assert message.message == "hello"
    assert message.addressed_to is None


def test_discussion_message_addressed_to():
    message = DiscussionMessage(
        day_number=1,
        speaker="Alice",
        message="Bram, where were you?",
        addressed_to="Bram",
    )
    assert message.addressed_to == "Bram"


def test_game_state_discussion_defaults_to_empty_list():
    state = GameState()
    assert state.discussion == []


def test_vote_record_defaults_to_abstain():
    vote = VoteRecord(day_number=2, voter="Alice")
    assert vote.target is None


def test_vote_record_with_target():
    vote = VoteRecord(day_number=2, voter="Alice", target="Bruce")
    assert vote.target == "Bruce"


def test_lynching_fields():
    lynching = Lynching(name="Bruce", day_number=2)
    assert lynching.name == "Bruce"
    assert lynching.day_number == 2


def test_game_state_votes_and_lynchings_default_to_empty_list():
    state = GameState()
    assert state.votes == []
    assert state.lynchings == []
