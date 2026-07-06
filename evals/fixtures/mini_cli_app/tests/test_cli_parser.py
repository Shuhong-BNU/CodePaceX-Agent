import pytest

from mini_cli.parser import parse_args, render_greeting


def test_count_must_be_positive():
    with pytest.raises(SystemExit):
        parse_args(["--count", "0"])


def test_render_greeting_repeats_count():
    assert render_greeting("Ada", 2) == "hello Ada\nhello Ada"
