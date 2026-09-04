"""AgentLoop 控制台跳转地址推导（自研观测面 → 云端技术信号的唯一缝合点）。

分工定稿（2026-09-04）：自研观测四页管实时业务观测（轨迹/用量/日志/告警），
AgentLoop（阿里云 ARMS）管技术信号与长期留存；全量遥测经既有
``otlp_endpoint``/``otlp_headers`` 持续上报云端，本地界面只在用户要深挖时
把人**送去**。本模块就是那条跳转地址的来源——不引入任何 SDK，只从部署里
**已经存在**的配置推导，推导不出就如实报 unconfigured，由前端出一次性
配置弹层，绝不要求用户自己去阿里云搜地址。

推导来源（全部是部署里已有的事实）：
- region：``otlp_endpoint`` 域名倒数第三段。ARMS/SLS 端点形如
  ``proj-xtrace-<id>-<region>.<region>.log.aliyuncs.com``；
- project / workspace：``otlp_headers`` 里的 ``x-arms-project`` /
  ``x-cms-workspace``（"k=v,k2=v2" 格式，见 settings 注释）。

默认落点是 **ARMS 控制台该地域首页**——保证有效的最保守地址；要深链到
具体页面，用 ``REPOMESH_AGENTLOOP_CONSOLE_URL`` 覆盖模板，支持
``{region}``/``{project}``/``{workspace}`` 占位符。
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: 全量遥测的上报域名特征（ARMS SLS 接入点）。域名的倒数第三段是地域。
_SLS_HOST_SUFFIX = ".log.aliyuncs.com"

#: 未配置覆盖时的默认跳转模板：ARMS 控制台该地域首页（必定有效的最保守落点）。
DEFAULT_CONSOLE_TEMPLATE = "https://arms.console.aliyun.com/?regionId={region}"

#: 返回结构里 source 字段的三种取值。
SOURCE_OVERRIDE = "override"
SOURCE_DERIVED = "derived"
SOURCE_UNCONFIGURED = "unconfigured"


def parse_otlp_headers(raw: str | None) -> dict[str, str]:
    """settings 里 "k=v,k2=v2" 形式的 OTLP 头 → 字典。坏段跳过不抛错。"""
    parsed: dict[str, str] = {}
    for segment in (raw or "").split(","):
        name, sep, value = segment.partition("=")
        if sep and name.strip() and value.strip():
            parsed[name.strip()] = value.strip()
    return parsed


def derive_region(endpoint: str | None) -> str | None:
    """从 OTLP 端点域名推 ARMS 地域；不是阿里云 SLS 接入点则 None。"""
    if not endpoint:
        return None
    host = urlsplit(endpoint).hostname or ""
    if not host.endswith(_SLS_HOST_SUFFIX):
        return None
    labels = host[: -len(_SLS_HOST_SUFFIX)].split(".")
    return labels[-1] if labels else None


def derive_project(endpoint: str | None) -> str | None:
    """项目标识取端点域名的首段（``proj-xtrace-<id>-<region>``）。"""
    if not endpoint:
        return None
    host = urlsplit(endpoint).hostname or ""
    if not host.endswith(_SLS_HOST_SUFFIX):
        return None
    labels = host[: -len(_SLS_HOST_SUFFIX)].split(".")
    return labels[0] if labels else None


def _fill_template(
    template: str,
    *,
    region: str | None,
    project: str | None,
    workspace: str | None,
) -> str:
    """占位符用 replace 而非 str.format：模板来自配置，多余花括号不该抛 KeyError。"""
    return (
        template.replace("{region}", region or "")
        .replace("{project}", project or "")
        .replace("{workspace}", workspace or "")
    )


def build_agentloop_config(
    *,
    endpoint: str | None,
    headers_raw: str | None,
    override_url: str | None,
) -> dict:
    """汇总跳转配置。优先级：显式覆盖模板 > 端点推导 > unconfigured。

    返回键：``configured`` / ``console_url`` / ``region`` / ``project`` /
    ``workspace`` / ``source``（override | derived | unconfigured）。
    前端约定：``configured=false`` 或拿不到该端点时出一次性配置弹层，
    用户粘贴的地址存本机，不回传服务端。
    """
    region = derive_region(endpoint)
    project = derive_project(endpoint)
    workspace = parse_otlp_headers(headers_raw).get("x-cms-workspace")

    override = (override_url or "").strip()
    if override:
        return {
            "configured": True,
            "console_url": _fill_template(
                override, region=region, project=project, workspace=workspace
            ),
            "region": region,
            "project": project,
            "workspace": workspace,
            "source": SOURCE_OVERRIDE,
        }

    if region is None:
        return {
            "configured": False,
            "console_url": None,
            "region": None,
            "project": project,
            "workspace": workspace,
            "source": SOURCE_UNCONFIGURED,
        }

    return {
        "configured": True,
        "console_url": _fill_template(
            DEFAULT_CONSOLE_TEMPLATE, region=region, project=project, workspace=workspace
        ),
        "region": region,
        "project": project,
        "workspace": workspace,
        "source": SOURCE_DERIVED,
    }
