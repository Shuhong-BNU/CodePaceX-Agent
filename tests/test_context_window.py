"""验证 CodePaceX 的模型上下文窗口解析。

覆盖正常流程、边界条件与错误路径，确保重构不会破坏既有行为契约。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from codepacex.client import create_client, resolve_context_window
from codepacex.config import ProviderConfig
from codepacex.tools.agent_tool import AgentTool
from codepacex.validator import (
    ConfigError,
    lookup_model_context_window,
    validate_providers,
)


# 测试场景
def _provider(**overrides) -> ProviderConfig:
    base = dict(
        name="p",
        protocol="anthropic",
        base_url="https://example.test",
        model="claude-sonnet-4-6",
        api_key="k",
    )
    base.update(overrides)
    return ProviderConfig(**base)


# ---------------------------------------------------------------------------
# 第 1 层 —— 配置中提供的值优先级最高
# ---------------------------------------------------------------------------

class TestConfigPriority:
    def test_explicit_config_wins_over_mapping_table(self):
        # claude 默认映射到 200000，但显式配置的 window 必须覆盖它。
        p = _provider(model="claude-sonnet-4-6", context_window=4096)
        assert p.get_context_window() == 4096

    def test_explicit_config_wins_over_fetched_value(self):
        p = _provider(context_window=4096)
        # 即便是缓存的自动获取值，也不能压过显式配置。
        p.set_fetched_context_window(999_000)
        assert p.get_context_window() == 4096

    def test_explicit_config_wins_over_default(self):
        # "mystery-model" 没有映射表项 → 本会默认到 128000。
        p = _provider(model="mystery-model", context_window=321_000)
        assert p.get_context_window() == 321_000


# ---------------------------------------------------------------------------
# 第 3 层 —— 内置映射表，对每类模型做子串匹配
# ---------------------------------------------------------------------------

class TestMappingTable:
    @pytest.mark.parametrize(
        "model, expected",
        [
            # 含 "1m" 子串（以及 "-1m" 后缀）-> 1,000,000
            ("claude-sonnet-4-6-1m", 1_000_000),
            ("some-model-1m", 1_000_000),
            ("gpt-4.1", 1_000_000),
            ("gpt-4.1-mini", 1_000_000),
            ("gpt-4o", 128_000),
            ("gpt-4o-mini", 128_000),
            ("gpt-4-turbo", 128_000),
            ("o1", 200_000),
            ("o1-preview", 200_000),
            ("o3-mini", 200_000),
            ("o4-mini", 200_000),
            ("gpt-3.5-turbo", 16_385),
            ("claude-opus-4-6", 200_000),
            ("CLAUDE-OPUS-4-6", 200_000),  # 大小写不敏感
        ],
    )
    def test_mapping_hits(self, model, expected):
        assert lookup_model_context_window(model) == expected
        # 在无配置、无自动获取的情况下，get_context_window 也必须返回相同结果。
        assert _provider(model=model).get_context_window() == expected

    def test_specificity_order_gpt_4_1_before_generic(self):
        # 即便没有更具体的匹配项，"gpt-4.1" 也必须胜出。
        assert lookup_model_context_window("gpt-4.1-nano") == 1_000_000

    def test_no_match_returns_zero(self):
        assert lookup_model_context_window("totally-unknown-model") == 0


# ---------------------------------------------------------------------------
# 第 4 层 —— 保守默认值
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_claude_default(self):
        # 没有其它线索的 claude 名称会命中 "claude" 映射表项。
        assert _provider(model="claude-future-99").get_context_window() == 200_000

    def test_unknown_model_default(self):
        assert _provider(model="some-llm-v2").get_context_window() == 128_000


# ---------------------------------------------------------------------------
# 第 2 层 —— 自动获取 + 缓存 + 优雅降级
# ---------------------------------------------------------------------------

class TestAutoFetch:
    @pytest.mark.asyncio
    async def test_fetch_success_is_cached_and_used(self):
        p = _provider(model="claude-sonnet-4-6")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(return_value=555_000)
        with patch("codepacex.client.create_client", return_value=fake) as mk:
            await resolve_context_window(p)
            # 此时第 2 层的值优先级高于映射表（200000）。
            assert p.get_context_window() == 555_000
            # 第二次解析绝不能再次发起网络请求（已缓存）。
            await resolve_context_window(p)
            mk.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_raises_degrades_to_mapping_table(self):
        p = _provider(model="claude-sonnet-4-6")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        with patch("codepacex.client.create_client", return_value=fake):
            # 不应抛出异常。
            await resolve_context_window(p)
        # 对 claude 回退到映射表。
        assert p.get_context_window() == 200_000

    @pytest.mark.asyncio
    async def test_fetch_returns_none_degrades_to_default(self):
        p = _provider(model="totally-unknown-model")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(return_value=None)
        with patch("codepacex.client.create_client", return_value=fake):
            await resolve_context_window(p)
        # 既没获取到、也没匹配到 → 使用保守默认值。
        assert p.get_context_window() == 128_000

    @pytest.mark.asyncio
    async def test_client_construction_failure_degrades(self):
        # 例如缺少 API key 会在 create_client 内部抛错 —— 必须被吞掉。
        p = _provider(model="claude-sonnet-4-6")
        with patch(
            "codepacex.client.create_client",
            side_effect=Exception("no api key"),
        ):
            await resolve_context_window(p)
        assert p.get_context_window() == 200_000

    @pytest.mark.asyncio
    async def test_non_anthropic_provider_is_not_fetched(self):
        p = _provider(protocol="openai-compat", model="gpt-4o")
        with patch("codepacex.client.create_client") as mk:
            await resolve_context_window(p)
            mk.assert_not_called()
        # 完全通过映射表解析。
        assert p.get_context_window() == 128_000

    @pytest.mark.asyncio
    async def test_explicit_config_skips_fetch(self):
        p = _provider(model="claude-sonnet-4-6", context_window=4096)
        with patch("codepacex.client.create_client") as mk:
            await resolve_context_window(p)
            mk.assert_not_called()
        assert p.get_context_window() == 4096

    @pytest.mark.asyncio
    async def test_zero_or_negative_fetch_is_ignored(self):
        p = _provider(model="claude-sonnet-4-6")
        fake = AsyncMock()
        fake.fetch_model_context_window = AsyncMock(return_value=0)
        with patch("codepacex.client.create_client", return_value=fake):
            await resolve_context_window(p)
        # 0 绝不能被缓存；仍然走映射表。
        assert p._fetched_context_window == 0
        assert p.get_context_window() == 200_000


# ---------------------------------------------------------------------------
# Validator —— 未设置的 context_window 保持为 0（表示「未设置」），并校验取值
# ---------------------------------------------------------------------------

class TestValidator:
    def test_unset_context_window_defaults_to_zero(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "anthropic",
                    "base_url": "u",
                    "model": "claude-sonnet-4-6",
                }
            ]
        )
        # 0 表示「未设置」；实际解析发生在调用 get_context_window() 时。
        assert cleaned[0]["context_window"] == 0

    def test_explicit_context_window_preserved(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "anthropic",
                    "base_url": "u",
                    "model": "claude-sonnet-4-6",
                    "context_window": 50_000,
                }
            ]
        )
        assert cleaned[0]["context_window"] == 50_000

    def test_legacy_model_only_config_still_works(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "openai-compat",
                    "base_url": "u",
                    "model": "qwen-plus",
                }
            ]
        )

        assert cleaned[0]["model"] == "qwen-plus"
        assert cleaned[0]["default_model"] == "qwen-plus"
        assert cleaned[0]["models"] == ["qwen-plus"]

    def test_default_model_and_models_config_is_normalized(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "openai-compat",
                    "base_url": "u",
                    "default_model": "qwen-plus",
                    "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
                }
            ]
        )

        assert cleaned[0]["model"] == "qwen-plus"
        assert cleaned[0]["default_model"] == "qwen-plus"
        assert cleaned[0]["models"] == ["qwen-plus", "qwen-turbo", "qwen-max"]

    def test_models_without_default_model_uses_first_model(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "openai-compat",
                    "base_url": "u",
                    "models": ["qwen-turbo", "qwen-max"],
                }
            ]
        )

        assert cleaned[0]["model"] == "qwen-turbo"
        assert cleaned[0]["default_model"] == "qwen-turbo"
        assert cleaned[0]["models"] == ["qwen-turbo", "qwen-max"]

    def test_models_empty_with_legacy_model_is_compatible(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "openai-compat",
                    "base_url": "u",
                    "model": "qwen-plus",
                    "models": [],
                }
            ]
        )

        assert cleaned[0]["model"] == "qwen-plus"
        assert cleaned[0]["default_model"] == "qwen-plus"
        assert cleaned[0]["models"] == ["qwen-plus"]

    def test_api_key_env_can_be_used_with_default_model_and_models(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "openai-compat",
                    "base_url": "u",
                    "api_key_env": "DASHSCOPE_API_KEY",
                    "default_model": "qwen-plus",
                    "models": ["qwen-plus", "qwen-turbo"],
                }
            ]
        )

        assert cleaned[0]["api_key_env"] == "DASHSCOPE_API_KEY"
        assert cleaned[0]["model"] == "qwen-plus"
        assert cleaned[0]["models"] == ["qwen-plus", "qwen-turbo"]

    def test_default_model_must_be_listed_in_models(self):
        with pytest.raises(ConfigError, match="default_model.*must be listed"):
            validate_providers(
                [
                    {
                        "name": "p",
                        "protocol": "openai-compat",
                        "base_url": "u",
                        "default_model": "qwen-plus",
                        "models": ["qwen-turbo"],
                    }
                ]
            )

    @pytest.mark.parametrize("bad", [123, True, [1]])
    def test_invalid_models_rejected(self, bad):
        with pytest.raises(ConfigError):
            validate_providers(
                [
                    {
                        "name": "p",
                        "protocol": "openai-compat",
                        "base_url": "u",
                        "models": bad,
                    }
                ]
            )

    @pytest.mark.parametrize(
        "entry",
        [
            {"model": ""},
            {"default_model": ""},
            {"models": ["qwen-plus", ""]},
        ],
    )
    def test_empty_model_names_rejected(self, entry):
        raw = {
            "name": "p",
            "protocol": "openai-compat",
            "base_url": "u",
        }
        raw.update(entry)
        with pytest.raises(ConfigError):
            validate_providers([raw])

    def test_missing_all_model_fields_is_rejected(self):
        with pytest.raises(ConfigError, match="must configure model"):
            validate_providers(
                [
                    {
                        "name": "p",
                        "protocol": "openai-compat",
                        "base_url": "u",
                    }
                ]
            )

    def test_api_key_env_defaults_to_empty_string(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "openai-compat",
                    "base_url": "u",
                    "model": "qwen-plus",
                }
            ]
        )
        assert cleaned[0]["api_key_env"] == ""

    def test_api_key_env_is_preserved(self):
        cleaned = validate_providers(
            [
                {
                    "name": "p",
                    "protocol": "openai-compat",
                    "base_url": "u",
                    "model": "qwen-plus",
                    "api_key_env": "DASHSCOPE_API_KEY",
                }
            ]
        )
        assert cleaned[0]["api_key_env"] == "DASHSCOPE_API_KEY"

    @pytest.mark.parametrize("bad", [123, True, ["OPENAI_API_KEY"]])
    def test_invalid_api_key_env_rejected(self, bad):
        with pytest.raises(ConfigError):
            validate_providers(
                [
                    {
                        "name": "p",
                        "protocol": "openai",
                        "base_url": "u",
                        "model": "gpt-5",
                        "api_key_env": bad,
                    }
                ]
            )

    @pytest.mark.parametrize("bad", [-1, "200000", True, 3.5])
    def test_invalid_context_window_rejected(self, bad):
        with pytest.raises(ConfigError):
            validate_providers(
                [
                    {
                        "name": "p",
                        "protocol": "anthropic",
                        "base_url": "u",
                        "model": "claude-sonnet-4-6",
                        "context_window": bad,
                    }
                ]
            )


class TestApiKeyResolution:
    def test_explicit_api_key_wins_over_api_key_env(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_KEY", "from-env")
        p = _provider(api_key="explicit", api_key_env="CUSTOM_KEY")

        assert p.resolve_api_key() == "explicit"

    def test_api_key_env_wins_over_protocol_default(self, monkeypatch):
        monkeypatch.setenv("CUSTOM_KEY", "custom")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "default")
        p = _provider(api_key="", api_key_env="CUSTOM_KEY")

        assert p.resolve_api_key() == "custom"

    def test_protocol_default_env_still_works_without_api_key_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "default")
        p = _provider(api_key="", api_key_env="")

        assert p.resolve_api_key() == "default"

    def test_missing_configured_api_key_env_does_not_fall_back_to_default(self, monkeypatch):
        monkeypatch.delenv("CUSTOM_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "default")
        p = _provider(api_key="", api_key_env="CUSTOM_KEY")

        assert p.resolve_api_key() == ""


class TestSubAgentProviderConfig:
    def test_model_override_preserves_api_key_env(self):
        provider = _provider(api_key="", api_key_env="CUSTOM_PROVIDER_KEY")
        tool = AgentTool(
            agent_loader=None,
            task_manager=None,
            trace_manager=None,
            parent_agent=None,
            provider_config=provider,
        )
        sentinel = object()

        with patch("codepacex.client.create_client", return_value=sentinel) as mk:
            assert tool._create_client_for_model("sonnet") is sentinel

        created_config = mk.call_args.args[0]
        assert created_config.api_key_env == "CUSTOM_PROVIDER_KEY"


class TestProviderModelSelection:
    def test_provider_config_prefers_default_model(self):
        p = ProviderConfig(
            name="p",
            protocol="openai-compat",
            base_url="https://example.test",
            model="legacy",
            default_model="qwen-plus",
            models=["qwen-plus", "qwen-turbo"],
            api_key="k",
        )

        assert p.model == "qwen-plus"
        assert p.default_model == "qwen-plus"
        assert p.models == ["qwen-plus", "qwen-turbo"]
        assert p.get_effective_model() == "qwen-plus"

    def test_provider_config_uses_first_model_without_default(self):
        p = ProviderConfig(
            name="p",
            protocol="openai-compat",
            base_url="https://example.test",
            models=["qwen-turbo", "qwen-max"],
            api_key="k",
        )

        assert p.model == "qwen-turbo"
        assert p.default_model == "qwen-turbo"
        assert p.models == ["qwen-turbo", "qwen-max"]

    def test_client_uses_effective_model(self):
        p = ProviderConfig(
            name="p",
            protocol="openai-compat",
            base_url="https://example.test",
            default_model="qwen-plus",
            models=["qwen-plus", "qwen-turbo"],
            api_key="k",
        )

        client = create_client(p)

        assert client.model == "qwen-plus"
