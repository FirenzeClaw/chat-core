"""Tests for EnergyBar (Spec 007)."""
import pytest
from chat_core.systems.energy import EnergyBar


class TestEnergyBar:
    """EnergyBar 单元测试 — 8 个测试用例。"""

    def test_normal_turn_consumes_003(self):
        """正常发言消耗 0.03。"""
        bar = EnergyBar()
        bar.consume(reply_count=1)
        assert bar.get_state().energy == pytest.approx(0.87)

    def test_long_reply_consumes_006(self):
        """长回复 (>3 段) 消耗 0.06。"""
        bar = EnergyBar()
        bar.consume(reply_count=5)
        assert bar.get_state().energy == pytest.approx(0.84)

    def test_emotion_shock_adds_010(self):
        """情绪冲击 (|Δcompound| > 0.4) 额外消耗 0.10。"""
        bar = EnergyBar()
        bar.consume(compound_delta=0.5)
        # 0.9 - 0.03 (基础) - 0.10 (情绪冲击) = 0.77
        assert bar.get_state().energy == pytest.approx(0.77)

    def test_defense_project_relief(self):
        """PROJECT 防御：精力 +0.02 解脱。"""
        bar = EnergyBar()
        bar.consume(has_defense_project=True)
        # 0.9 + 0.02 (relief) - 0.03 (基础) = 0.89
        assert bar.get_state().energy == pytest.approx(0.89)

    def test_defense_denial_drain(self):
        """DENIAL 防御：精力额外消耗（-0.04 防御基础 -0.02 denial_drain）。"""
        bar = EnergyBar()
        bar.consume(has_defense_denial=True)
        # 0.9 - 0.03 (基础) - 0.04 (defense) - 0.02 (denial_drain) = 0.81
        assert bar.get_state().energy == pytest.approx(0.81)

    def test_recovery_high(self):
        """高分位恢复 (>0.6): 60s → +0.02。"""
        bar = EnergyBar()
        bar._state.energy = 0.8
        bar.recover(60)
        assert bar.get_state().energy == pytest.approx(0.82)

    def test_recovery_low(self):
        """低分位恢复 (<0.3): 60s → +0.005。"""
        bar = EnergyBar()
        bar._state.energy = 0.2
        bar.recover(60)
        assert bar.get_state().energy == pytest.approx(0.205)

    def test_exit_threshold(self):
        """energy < 0.15 → should_exit() 返回 True。"""
        bar = EnergyBar()
        bar._state.energy = 0.10
        assert bar.should_exit() is True

    def test_should_exit_above_threshold(self):
        """energy >= 0.15 → should_exit() 返回 False。"""
        bar = EnergyBar()
        bar._state.energy = 0.16
        assert bar.should_exit() is False
