from mini_cli.commands import command_description, resolve_command


def test_alias_resolves_to_canonical_command():
    assert resolve_command("ls") == "list"
    assert command_description("ls") == "List configured items"
