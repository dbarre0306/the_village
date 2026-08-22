from the_village.state import WEEKDAYS, Day, DiscussionMessage, GameState, Player, VoteRecord


def test_player_defaults():
    player = Player(name="Alice", player_type="villager")
    assert player.is_pack_leader is False
    assert player.is_alive is True


def test_weekdays_starts_on_sunday():
    assert WEEKDAYS[0] == "Sunday"
    assert len(WEEKDAYS) == 7


def test_day_defaults():
    day = Day(day_number=1)
    assert day.player_killed is None
    assert day.discussion == []
    assert day.votes == []
    assert day.player_lynched is None


def test_discussion_message_fields():
    message = DiscussionMessage(player_name="Alice", text="hello")
    assert message.player_name == "Alice"
    assert message.text == "hello"
    assert message.addressed_to is None


def test_discussion_message_addressed_to():
    message = DiscussionMessage(
        player_name="Alice",
        text="Bram, where were you?",
        addressed_to="Bram",
    )
    assert message.addressed_to == "Bram"


def test_vote_record_defaults_to_abstain():
    vote = VoteRecord(voter="Alice")
    assert vote.target is None


def test_vote_record_with_target():
    vote = VoteRecord(voter="Alice", target="Bruce")
    assert vote.target == "Bruce"


def test_game_state_defaults():
    state = GameState()
    assert state.user_player_name == ""
    assert state.players == []
    assert state.days == [Day(day_number=1)]


def test_game_state_day_number_property_reads_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_killed="Bruce")])
    assert state.day_number == 2


def test_game_state_current_day_property_returns_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_killed="Bruce")])
    assert state.current_day == Day(day_number=2, player_killed="Bruce")


def test_advance_day_appends_a_new_day_and_returns_it():
    state = GameState()
    new_day = state.advance_day(player_killed="Bruce")

    assert new_day == Day(day_number=2, player_killed="Bruce")
    assert state.days == [Day(day_number=1), Day(day_number=2, player_killed="Bruce")]
    assert state.current_day == new_day


def test_advance_day_with_no_death():
    state = GameState()
    new_day = state.advance_day()

    assert new_day.player_killed is None
    assert state.day_number == 2
