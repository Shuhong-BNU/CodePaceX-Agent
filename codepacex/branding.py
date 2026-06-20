"""集中定义 CodePaceX 的产品标识、版本读取与终端 Banner 渲染。

该模块为 TUI 和远程界面提供统一的品牌名称与短标识，避免各入口分别硬编码
Logo 或版本号。版本优先读取已安装分发包的元数据；直接从未安装源码运行时，
使用 ``dev`` 明确表示开发构建。
"""

from __future__ import annotations

from importlib import metadata

from rich.text import Text as RichText


PRODUCT_NAME = "CodePaceX"
DISTRIBUTION_NAME = "codepacex-agent"
SHORT_MARK = "▸_▸ CPX"
REMOTE_NAME = f"{PRODUCT_NAME} Remote"

TERMINAL_BADGE = (
    "╭─────╮",
    "│ ▸_▸ │",
    "╰─CPX─╯",
)


def get_version() -> str:
    """返回当前安装的 CodePaceX 版本，源码开发环境缺少元数据时返回 dev。"""
    try:
        return metadata.version(DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return "dev"


def build_terminal_banner(model: str = "", work_dir: str = "") -> RichText:
    """构建固定三行的 TUI 品牌 Banner，并附带模型与当前工作目录。"""
    details = (
        f"{PRODUCT_NAME} v{get_version()}",
        model,
        work_dir,
    )
    banner = RichText()
    for index, (badge_line, detail) in enumerate(zip(TERMINAL_BADGE, details)):
        banner.append(f"{badge_line}    ", style="bold color(99)")
        banner.append(detail, style="color(242)")
        if index < len(TERMINAL_BADGE) - 1:
            banner.append("\n")
    return banner
