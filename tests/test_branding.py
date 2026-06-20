"""验证 CodePaceX 品牌标识在 TUI 与远程界面中的统一渲染行为。

测试覆盖动态版本读取、三行终端徽章、开发环境回退和远程页面品牌文本，确保
旧猫图案、闪电标识与硬编码版本不会重新进入用户界面。
"""

from __future__ import annotations

from html import unescape
from importlib import metadata, reload

import pytest
from textual.widgets import Static

import codepacex.branding as branding
from codepacex.app import CodePaceXApp
from codepacex.config import ProviderConfig


# ---------------------------------------------------------------------------
# 版本与终端 Banner
# ---------------------------------------------------------------------------


def test_terminal_banner_uses_badge_and_dynamic_version(monkeypatch) -> None:
    monkeypatch.setattr(branding.metadata, "version", lambda _: "9.8.7")

    lines = branding.build_terminal_banner("test-model", "/tmp/project").plain.splitlines()

    assert lines == [
        "╭─────╮    CodePaceX v9.8.7",
        "│ ▸_▸ │    test-model",
        "╰─CPX─╯    /tmp/project",
    ]


def test_version_falls_back_to_dev_without_distribution(monkeypatch) -> None:
    def missing_distribution(_: str) -> str:
        raise metadata.PackageNotFoundError

    monkeypatch.setattr(branding.metadata, "version", missing_distribution)

    assert branding.get_version() == "dev"


def test_terminal_banner_contains_no_legacy_cat() -> None:
    plain = branding.build_terminal_banner().plain
    legacy_cat = (
        "/" + "\\_/" + "\\",
        "( " + "o.o" + " )",
        "> " + "^" + " <",
    )

    assert all(fragment not in plain for fragment in legacy_cat)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 43)])
async def test_terminal_banner_fits_supported_terminal_sizes(size) -> None:
    providers = [
        ProviderConfig("first", "openai", "https://example.com", "model-a"),
        ProviderConfig("second", "openai", "https://example.com", "model-b"),
    ]
    app = CodePaceXApp(providers=providers)

    async with app.run_test(size=size):
        title_bar = app.query_one("#title-bar", Static)

        assert title_bar.size.width == size[0] - 2
        assert title_bar.size.height == 3
        assert "╭─────╮" in title_bar.render().plain


# ---------------------------------------------------------------------------
# Remote 品牌文本
# ---------------------------------------------------------------------------


def test_remote_html_uses_shared_brand(monkeypatch) -> None:
    monkeypatch.setattr(branding.metadata, "version", lambda _: "0.2.0")

    # web_content 在导入时生成静态 HTML，因此重新加载以应用模拟版本。
    import codepacex.web_content as web_content

    html = unescape(reload(web_content).INDEX_HTML)

    assert "▸_▸ CPX · CodePaceX Remote · v0.2.0" in html
    assert ("⚡ " + "CodePaceX Remote") not in html
