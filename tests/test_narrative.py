"""Tests for Spec 010: NarrativeEngine — chapter append, timeline, injection"""

import pytest
from chat_core.systems.narrative import NarrativeEngine


class TestNarrativeEngineChapters:
    """事件驱动章节测试"""

    def test_append_vulnerability_chapter(self):
        ne = NarrativeEngine()
        ne.append_chapter("vulnerability", "我在对话中暴露了脆弱", turn=10)
        assert len(ne.state.chapters) == 1
        assert ne.state.chapters[0].event_type == "vulnerability"
        assert ne.state.chapters[0].text == "我在对话中暴露了脆弱"
        assert ne.state.chapters[0].turn == 10

    def test_append_multiple_chapters(self):
        ne = NarrativeEngine()
        ne.append_chapter("vulnerability", "脆弱1", turn=1)
        ne.append_chapter("deep_memory", "深刻记忆", turn=2)
        ne.append_chapter("vulnerability", "脆弱2", turn=3)
        assert len(ne.state.chapters) == 3

    def test_chapter_limit_enforced(self):
        ne = NarrativeEngine()
        for i in range(60):
            ne.append_chapter("deep_memory", f"记忆{i}", turn=i)
        assert len(ne.state.chapters) == 50


class TestNarrativeEngineInjection:
    """System prompt 注入测试"""

    def test_get_system_injection_empty(self):
        ne = NarrativeEngine()
        result = ne.get_system_injection()
        assert result == ""

    def test_get_system_injection_with_latest(self):
        ne = NarrativeEngine()
        ne.update_latest("我是一个倾向于真实的人，但有时会犹豫。")
        ne.append_chapter("vulnerability", "最近暴露了脆弱", turn=5)
        result = ne.get_system_injection()
        assert "[自我叙述]" in result
        assert "倾向于真实" in result
        assert "[最近的思考]" in result

    def test_update_latest_overwrites(self):
        ne = NarrativeEngine()
        ne.update_latest("第一版")
        ne.update_latest("第二版")
        assert ne.state.latest == "第二版"


class TestNarrativeContext:
    """叙事上下文组装测试"""

    def test_build_narrative_context(self):
        ne = NarrativeEngine()
        ne.update_latest("我是真实的人。")
        ctx = ne.build_narrative_context()
        assert "当前价值观" in ctx
        assert "最近的经历" in ctx
        assert "上一版自我叙述" in ctx
        assert "我是真实的人" in ctx
