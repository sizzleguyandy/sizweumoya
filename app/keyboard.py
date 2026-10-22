"""The buttons under each person's text box. A tap sends the label as a normal message,
which Sarah answers like any other (see "Buttons" in SOUL.md)."""
from . import hermes

TODAY = "Today's plan"
WEEK = "My week"
BUTTONS = [[TODAY, WEEK]]
INTRO = "I've added two shortcuts below the message box: tap them any time."


def send(runner, text: str = INTRO) -> None:
    hermes.send_keyboard(runner["telegram_chat_id"], text, BUTTONS)
