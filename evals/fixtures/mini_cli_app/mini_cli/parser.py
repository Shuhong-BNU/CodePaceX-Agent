from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mini-cli")
    parser.add_argument("--name", default="world")
    parser.add_argument("--count", type=int, default=1)
    return parser


def parse_args(argv: list[str]) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def render_greeting(name: str, count: int) -> str:
    return "\n".join([f"hello {name}"] * count)
