"""issue 标题锚点（service._title）的提炼优先级测试。

背景（2026-09-08 用户裁决）：需求是一整篇贴进来的文档时，标题取需求文本
前 80 字符就成了「文档开头节选」。需求分析（发现链步 1）落块后带
``suggested_title``，标题必须优先用它；分析未到/没给/空白时退回截断。
"""

from __future__ import annotations

from uuid import uuid4

from repomesh.api.read_models.service import _title


def _discovery_with(title: str) -> dict:
    return {"analysis": {"suggested_title": title}}


class TestTitlePrefersDistilled:
    def test_uses_suggested_title_over_truncation(self) -> None:
        doc = "库存管理系统技术方案\n\n第一章 系统背景……" + "很长的正文" * 40
        result = _title(doc, uuid4(), _discovery_with("库存服务接入审计日志"))
        assert result == "库存服务接入审计日志"

    def test_falls_back_to_truncation_without_analysis(self) -> None:
        doc = "长" * 100
        result = _title(doc, uuid4(), None)
        assert result == "长" * 77 + "..."

    def test_falls_back_when_analysis_block_missing(self) -> None:
        result = _title("短需求", uuid4(), {})
        assert result == "短需求"

    def test_falls_back_when_suggested_title_blank(self) -> None:
        """模型给了空白串等于没给——不能让空白标题上榜。"""
        result = _title("短需求", uuid4(), _discovery_with("   "))
        assert result == "短需求"

    def test_falls_back_when_suggested_title_not_string(self) -> None:
        block = {"analysis": {"suggested_title": 123}}
        result = _title("短需求", uuid4(), block)
        assert result == "短需求"

    def test_distilled_title_capped_at_eighty(self) -> None:
        result = _title("x", uuid4(), _discovery_with("标" * 200))
        assert result == "标" * 80

    def test_no_requirement_text_still_uses_distilled_title(self) -> None:
        """requirement_text 缺失但分析在，提炼标题照样优先于占位符。"""
        result = _title(None, uuid4(), _discovery_with("库存服务接入审计日志"))
        assert result == "库存服务接入审计日志"

    def test_both_missing_returns_placeholder(self) -> None:
        project_id = uuid4()
        assert _title(None, project_id, None) == f"Project {str(project_id)[:8]}"
