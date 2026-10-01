"""The AI handoff summary is read as plain text.

The console shows internal notes exactly as written, because people write
them too. Found while capturing the user guide (2026-10-01): the summary began
with markdown bold, so every colleague picking up a handoff saw literal `**`.
"""

from iam_platform.application.ai_resources.public_handoff import _extractive_summary


def test_the_summary_carries_no_markdown_emphasis() -> None:
    summary = _extractive_summary(
        [
            ("user", "How long must he stay off after a sickness bug?"),
            ("assistant", "Children must stay away for **48 hours after the last episode**. [1]"),
            ("user", "Can I speak to a person?"),
        ],
        "Office team",
    )

    assert "**" not in summary
    assert summary.startswith("AI handoff summary — routed to Office team.")
    assert "48 hours after the last episode" in summary
    assert "- Most recent question: Can I speak to a person?" in summary
