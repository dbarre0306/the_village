from the_village.discussion.speaker import DECLINED_TO_RESPOND
from the_village.state import WEEKDAYS, Day, DiscussionMessage, GameState, Player, VoteRecord


def test_player_defaults():
    player = Player(name="Alice", player_type="villager")
    assert player.is_pack_leader is False
    assert player.is_alive is True


def test_weekdays_starts_on_monday_ends_on_sunday():
    assert WEEKDAYS[0] == "Monday"
    assert WEEKDAYS[-1] == "Sunday"
    assert len(WEEKDAYS) == 7


def test_day_defaults():
    day = Day(day_number=1)
    assert day.player_found_dead is None
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
    vote = VoteRecord(voter_name="Alice")
    assert vote.target_name is None


def test_vote_record_with_target():
    vote = VoteRecord(voter_name="Alice", target_name="Bruce")
    assert vote.target_name == "Bruce"


def test_game_state_defaults():
    state = GameState()
    assert state.user_player_name == ""
    assert state.players == []
    assert state.days == [Day(day_number=1)]


def test_game_state_day_number_property_reads_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_found_dead="Bruce")])
    assert state.day_number == 2


def test_game_state_current_day_property_returns_the_last_day():
    state = GameState(days=[Day(day_number=1), Day(day_number=2, player_found_dead="Bruce")])
    assert state.current_day == Day(day_number=2, player_found_dead="Bruce")


def test_advance_day_appends_a_new_day_and_returns_it():
    state = GameState()
    new_day = state.advance_day()

    assert new_day == Day(day_number=2)
    assert state.days == [Day(day_number=1), Day(day_number=2)]
    assert state.current_day == new_day
    assert state.day_number == 2


def test_names_of_living_players_excludes_the_dead():
    players = [
        Player(name="Alice", player_type="villager"),
        Player(name="Bram", player_type="villager", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.names_of_living_players() == ["Alice"]


def test_names_of_other_living_players_excludes_the_given_player_and_the_dead():
    players = [
        Player(name="Alice", player_type="villager"),
        Player(name="Bram", player_type="villager"),
        Player(name="Cass", player_type="villager", is_alive=False),
    ]
    state = GameState(players=players)
    assert state.names_of_other_living_players("Alice") == ["Bram"]


def test_last_player_to_speak_returns_none_with_no_discussion():
    state = GameState()
    assert state.last_player_to_speak() is None


def test_last_player_to_speak_returns_the_last_speaker():
    state = GameState()
    state.current_day.discussion = [
        DiscussionMessage(player_name="Alice", text="hello"),
        DiscussionMessage(player_name="Bram", text="hi Alice"),
    ]

    assert state.last_player_to_speak() == "Bram"


def test_last_player_to_speak_returns_the_decliner_so_they_dont_speak_again_right_away():
    state = GameState()
    state.current_day.discussion = [
        DiscussionMessage(player_name="Alice", text="hello"),
        DiscussionMessage(player_name="Bram", text=DECLINED_TO_RESPOND),
    ]

    assert state.last_player_to_speak() == "Bram"


def test_format_current_day_names_the_weekday_for_the_current_day_number():
    state = GameState(user_player_name="Dana", days=[Day(day_number=2)])
    assert state.format_current_day() == "Today is Tuesday."


def test_format_deaths_with_no_deaths():
    state = GameState(user_player_name="Dana")
    assert state.format_deaths() == "(No one has been killed by the werewolves yet.)"


def test_format_deaths_lists_each_death():
    state = GameState(
        user_player_name="Dana", days=[Day(day_number=2, player_found_dead="D")]
    )
    assert state.format_deaths() == "D was killed by the werewolves on Tuesday."


def test_format_history_with_no_messages():
    state = GameState(user_player_name="Dana")
    assert state.format_history() == "(No discussion has happened yet.)"


def test_format_history_includes_prior_days_in_order():
    state = GameState(
        user_player_name="Dana",
        days=[
            Day(
                day_number=1,
                discussion=[
                    DiscussionMessage(player_name="A", text="yesterday's message")
                ],
            ),
            Day(
                day_number=2,
                discussion=[DiscussionMessage(player_name="B", text="today's message")],
            ),
        ],
    )
    assert (
        state.format_history() == "A: yesterday's message\nB: today's message"
    )
