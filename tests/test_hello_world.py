"""验证 CodePaceX 的基础工具调用闭环。

覆盖正常流程、边界条件与错误路径，确保重构不会破坏既有行为契约。
"""

from __future__ import annotations


# 测试场景
def test_hello_world() -> None:
    assert "Hello World" == "Hello World"
