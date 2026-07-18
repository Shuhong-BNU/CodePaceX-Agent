from mini_multi.commands import resolve_command
from mini_multi.parser import parse_command


def test_parser_and_aliases_normalize_case_and_space() -> None:
    assert parse_command("  RUN : value ") == ("run", "value")
    assert parse_command(" status ") == ("status", "")
    assert resolve_command(" LS ") == "list"
    assert resolve_command("Unknown") == "unknown"
