"""提供 CodePaceX 的斜杠命令和文件引用补全界面能力。

主要包含斜杠命令的解析、注册、补全与处理器。该模块由终端应用的命令分发层调用，并维护命令参数和 UI 状态一致性。
"""

from __future__ import annotations

from textual.message import Message as TMessage
from textual.widgets import Static


# 核心实现
class CompletionPopup(Static):

    DEFAULT_CSS = """
    CompletionPopup {
        height: auto;
        max-height: 8;
        display: none;
        padding: 0 1;
    }
    """

    class Selected(TMessage):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._displays: list[str] = []
        self._values: list[str] = []
        self._cursor: int = 0

    def show_pairs(self, pairs: list[tuple[str, str]]) -> None:
        """以 (display_text, value) 对的形式显示候选项。"""
        self._displays = [d for d, _ in pairs]
        self._values = [v for _, v in pairs]
        self._cursor = 0
        self._refresh_content()
        self.display = True

    def show(self, items: list[str]) -> None:
        self.show_pairs([(i, i) for i in items])

    def hide(self) -> None:
        self.display = False
        self._displays = []
        self._values = []
        self._cursor = 0

    @property
    def is_visible(self) -> bool:
        return bool(self.display)

    def move_up(self) -> None:
        if self._displays and self._cursor > 0:
            self._cursor -= 1
            self._refresh_content()

    def move_down(self) -> None:
        if self._displays and self._cursor < len(self._displays) - 1:
            self._cursor += 1
            self._refresh_content()

    def get_selected(self) -> str | None:
        if not self._values:
            return None
        return self._values[self._cursor]

    def _refresh_content(self) -> None:
        lines = []
        for i, display in enumerate(self._displays):
            if i == self._cursor:
                lines.append(f"[bold reverse] {display} [/]")
            else:
                lines.append(f"  [dim]{display}[/]")
        self.update("\n".join(lines))

    def on_click(self) -> None:
        selected = self.get_selected()
        if selected:
            self.post_message(self.Selected(selected))
            self.hide()
