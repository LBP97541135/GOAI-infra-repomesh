"""AgentLoop 跳转配置推导的纯函数单测（无数据库、无网络）。

推导规则来自 ``infrastructure/agentloop.py``：地域取 OTLP 端点域名倒数
第三段，project/workspace 取 OTLP 头，显式覆盖模板优先级最高。样例值
与 ``.env.example`` / 2026-09-04 实际部署的端点形状一致。
"""

from repomesh.modules.observability.infrastructure.agentloop import (
    build_agentloop_config,
    parse_otlp_headers,
)

_ENDPOINT = (
    "https://proj-xtrace-2eb66b38475880aa4d34533055742318-cn-hangzhou"
    ".cn-hangzhou.log.aliyuncs.com/apm/trace/opentelemetry"
)
_HEADERS = (
    "x-arms-license-key=j2mvbfwr8q@92c1c70037310fa,"
    "x-arms-project=proj-xtrace-2eb66b38475880aa4d34533055742318-cn-hangzhou,"
    "x-cms-workspace=agentloop-f6b04100cd32575d72ff2676eabf7af7"
)


def test_parse_otlp_headers_splits_pairs_and_skips_bad_segments():
    assert parse_otlp_headers("a=1, b=2") == {"a": "1", "b": "2"}
    assert parse_otlp_headers("good=1,,broken,novalue=,x=y") == {"good": "1", "x": "y"}
    assert parse_otlp_headers(None) == {}
    assert parse_otlp_headers("") == {}


def test_derived_config_from_aliyun_endpoint():
    config = build_agentloop_config(endpoint=_ENDPOINT, headers_raw=_HEADERS, override_url=None)
    assert config["configured"] is True
    assert config["source"] == "derived"
    assert config["region"] == "cn-hangzhou"
    assert config["project"] == "proj-xtrace-2eb66b38475880aa4d34533055742318-cn-hangzhou"
    assert config["workspace"] == "agentloop-f6b04100cd32575d72ff2676eabf7af7"
    # 默认模板落 ARMS 控制台该地域首页——必定有效的最保守地址
    assert config["console_url"] == "https://arms.console.aliyun.com/?regionId=cn-hangzhou"


def test_non_aliyun_endpoint_is_unconfigured_not_guessed():
    config = build_agentloop_config(
        endpoint="http://localhost:4318/v1/traces", headers_raw=None, override_url=None
    )
    assert config["configured"] is False
    assert config["source"] == "unconfigured"
    assert config["console_url"] is None
    assert config["region"] is None


def test_missing_endpoint_is_unconfigured():
    config = build_agentloop_config(endpoint=None, headers_raw=None, override_url=None)
    assert config["configured"] is False
    assert config["source"] == "unconfigured"


def test_override_template_wins_and_fills_placeholders():
    config = build_agentloop_config(
        endpoint=_ENDPOINT,
        headers_raw=_HEADERS,
        override_url="https://example.com/agentloop?r={region}&p={project}&w={workspace}",
    )
    assert config["configured"] is True
    assert config["source"] == "override"
    assert config["console_url"].startswith("https://example.com/agentloop?r=cn-hangzhou&p=")
    assert "w=agentloop-f6b04100cd32575d72ff2676eabf7af7" in config["console_url"]


def test_override_template_with_stray_braces_does_not_raise():
    # 模板来自配置：多余花括号必须原样通过，不允许 str.format 抛 KeyError
    config = build_agentloop_config(
        endpoint=_ENDPOINT,
        headers_raw=_HEADERS,
        override_url="https://example.com/{unknown}?r={region}",
    )
    assert config["configured"] is True
    assert "{unknown}" in config["console_url"]
    assert "r=cn-hangzhou" in config["console_url"]


def test_override_applies_even_without_derivable_region():
    # 显式覆盖时允许地域推导失败：用户自己给了完整地址，占位符填空串即可
    config = build_agentloop_config(
        endpoint="http://localhost:4318/v1/traces", headers_raw=None, override_url="https://arms.console.aliyun.com"
    )
    assert config["configured"] is True
    assert config["source"] == "override"
    assert config["console_url"] == "https://arms.console.aliyun.com"
