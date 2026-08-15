import logging

import gradio as gr

from the_village.discussion import AdvanceStatus, DiscussionRunner, advance, start_discussion
from the_village.main import VillageFlow
from the_village.state import WEEKDAYS, GameState

logger = logging.getLogger(__name__)


def format_event_log(state: GameState) -> str:
    if not state.deaths:
        return "Nothing has happened yet."
    lines = [
        f"{WEEKDAYS[(death.day_number - 1) % 7]} morning: {death.name} was found dead."
        for death in state.deaths
    ]
    return "\n\n".join(lines)


def format_deaths_panel(state: GameState) -> str:
    if not state.deaths:
        return "No one has been killed yet."
    lines = [
        f"- {WEEKDAYS[(death.day_number - 1) % 7]}: {death.name}"
        for death in state.deaths
    ]
    return "\n".join(lines)


def format_alive_panel(state: GameState) -> str:
    alive = [villager for villager in state.villagers if villager.is_alive]
    if not alive:
        return "No one is left."
    lines = [
        f"- {villager.name} (me)" if villager.name == state.player_name else f"- {villager.name}"
        for villager in alive
    ]
    return "\n".join(lines)


def format_discussion_transcript(state: GameState) -> str:
    if not state.discussion:
        return "The discussion hasn't started yet."
    lines = [f"**{m.speaker}:** {m.message}" for m in state.discussion]
    return "\n\n".join(lines)


def _living_ai_names(state: GameState) -> list[str]:
    return [
        villager.name
        for villager in state.villagers
        if villager.is_alive and villager.player_type in ("villager", "werewolf")
    ]


def _drive_discussion(runner, events, dropdown_update=None):
    dropdown_update = dropdown_update if dropdown_update is not None else gr.update()
    try:
        for event in events:
            transcript = format_discussion_transcript(runner.state)
            if isinstance(event, AdvanceStatus):
                complete = event == AdvanceStatus.COMPLETE
                yield (
                    runner,
                    transcript,
                    dropdown_update,
                    gr.update(visible=not complete),
                    gr.update(
                        visible=complete,
                        value="The discussion has ended." if complete else "",
                    ),
                )
            else:
                yield (runner, transcript, dropdown_update, gr.update(), gr.update())
            dropdown_update = gr.update()
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Discussion turn failed")
        raise gr.Error("Something went wrong, please try again.") from exc


def begin_discussion(state: GameState):
    runner = start_discussion(state)
    yield from _drive_discussion(
        runner,
        advance(runner),
        dropdown_update=gr.update(choices=_living_ai_names(state)),
    )


def send_discussion_turn(runner: DiscussionRunner, message: str, addressed_to: str):
    events = advance(
        runner,
        player_input=message.strip() or None,
        player_addressed_to=addressed_to or None,
    )
    yield from _drive_discussion(runner, events)


def pass_discussion_turn(runner: DiscussionRunner):
    yield from _drive_discussion(runner, advance(runner, player_pass=True))


def start_game(player_name: str):
    if not player_name or not player_name.strip():
        raise gr.Error("Please enter your name.")

    try:
        flow = VillageFlow()
        flow.kickoff(inputs={"player_name": player_name.strip()})
    except gr.Error:
        raise
    except Exception as exc:
        logger.exception("Flow kickoff failed")
        raise gr.Error("Something went wrong, please try again.") from exc

    state = flow.state
    return (
        gr.update(visible=False),
        gr.update(visible=True),
        format_event_log(state),
        format_deaths_panel(state),
        format_alive_panel(state),
        state,
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="The Village") as demo:
        game_state = gr.State()
        discussion_runner_state = gr.State()

        with gr.Column(visible=True) as start_screen:
            name_input = gr.Textbox(label="Your first name")
            start_button = gr.Button("Start Game")

        with gr.Row(visible=False) as result_screen:
            with gr.Column():
                gr.Markdown("### Events")
                event_log = gr.Markdown()
                begin_discussion_button = gr.Button("Begin Discussion")
                discussion_transcript = gr.Markdown()
                with gr.Row(visible=False) as discussion_input_row:
                    discussion_textbox = gr.Textbox(label="Say something", scale=3)
                    discussion_addressed_to = gr.Dropdown(
                        label="Address to (optional)", choices=[], scale=1
                    )
                    send_button = gr.Button("Send")
                    pass_button = gr.Button("Pass")
                discussion_status = gr.Markdown(visible=False)
            with gr.Column():
                gr.Markdown("### Alive Villagers")
                alive_panel = gr.Markdown()
                gr.Markdown("### Killed by Werewolves")
                deaths_panel = gr.Markdown()

        start_button.click(
            fn=start_game,
            inputs=[name_input],
            outputs=[
                start_screen,
                result_screen,
                event_log,
                deaths_panel,
                alive_panel,
                game_state,
            ],
            concurrency_limit=None,
        )

        discussion_outputs = [
            discussion_runner_state,
            discussion_transcript,
            discussion_addressed_to,
            discussion_input_row,
            discussion_status,
        ]

        begin_discussion_button.click(
            fn=begin_discussion,
            inputs=[game_state],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        send_button.click(
            fn=send_discussion_turn,
            inputs=[discussion_runner_state, discussion_textbox, discussion_addressed_to],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

        pass_button.click(
            fn=pass_discussion_turn,
            inputs=[discussion_runner_state],
            outputs=discussion_outputs,
            concurrency_limit=None,
        )

    return demo


def main():
    build_app().launch()


if __name__ == "__main__":
    main()
