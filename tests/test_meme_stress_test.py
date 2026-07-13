"""
run_meme_stress_test.py 的单元测试（全局扫描修复：此前零测试覆盖）。

重点覆盖"核心审判结论"直接依赖的两个函数的索引/日期算术：
    - _compute_coverage_completeness：主升浪核心段覆盖完整度公式
    - _epic_pool_cross_section：Lead Time（peak_idx - entry_idx）与覆盖
      完整度的横截面统计，以及"崩溃资产"判定逻辑

reporter 参数只用到 .logs_df 这一个属性，用 SimpleNamespace 构造一个
轻量替身即可，不需要真实的 BacktestReporter/数据库。
"""
from types import SimpleNamespace

import pandas as pd
import pytest

from run_meme_stress_test import (
    CRASH_DRAWDOWN_THRESHOLD,
    _compute_coverage_completeness,
    _epic_pool_cross_section,
)
from state_machine.constants import LifecycleStage


def _make_price_df(symbol: str, closes: list[float]) -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"timestamp": timestamps, "symbol": symbol, "close": closes})


class TestComputeCoverageCompleteness:
    def test_exit_exactly_at_peak_gives_full_coverage(self):
        # 价格：100 -> 130（峰值）-> 90，入场价 100，恰好在峰值离场
        price_df = _make_price_df("TESTUSDT", [100.0, 110.0, 130.0, 120.0, 90.0])
        entry_ts = price_df["timestamp"].iloc[0]
        exit_ts = price_df["timestamp"].iloc[2]  # 峰值那一天离场

        coverage = _compute_coverage_completeness("TESTUSDT", entry_ts, exit_ts, price_df)

        assert coverage == pytest.approx(1.0)

    def test_exit_after_giving_back_all_gains_gives_zero_coverage(self):
        # 入场价 100，峰值 130，离场时又跌回 100——覆盖完整度应为 0
        price_df = _make_price_df("TESTUSDT", [100.0, 130.0, 110.0, 100.0])
        entry_ts = price_df["timestamp"].iloc[0]
        exit_ts = price_df["timestamp"].iloc[3]

        coverage = _compute_coverage_completeness("TESTUSDT", entry_ts, exit_ts, price_df)

        assert coverage == pytest.approx(0.0)

    def test_no_exit_uses_last_available_price_as_window_end(self):
        # exit_ts_or_none=None 时应该用数据末尾作为窗口终点
        price_df = _make_price_df("TESTUSDT", [100.0, 120.0, 150.0, 130.0])
        entry_ts = price_df["timestamp"].iloc[0]

        coverage = _compute_coverage_completeness("TESTUSDT", entry_ts, None, price_df)

        # peak=150, entry=100, exit(最后一条)=130 -> (130-100)/(150-100)=0.6
        assert coverage == pytest.approx(0.6)

    def test_returns_none_when_peak_never_exceeds_entry_price(self):
        # 入场后价格只跌不涨，从未创出新高——不构成"覆盖了多少主升浪"
        price_df = _make_price_df("TESTUSDT", [100.0, 90.0, 80.0, 70.0])
        entry_ts = price_df["timestamp"].iloc[0]

        coverage = _compute_coverage_completeness("TESTUSDT", entry_ts, None, price_df)

        assert coverage is None

    def test_returns_none_when_symbol_not_in_price_df(self):
        price_df = _make_price_df("OTHERUSDT", [100.0, 110.0])
        entry_ts = price_df["timestamp"].iloc[0]

        coverage = _compute_coverage_completeness("TESTUSDT", entry_ts, None, price_df)

        assert coverage is None


class TestEpicPoolCrossSection:
    def _make_logs_df(self, symbol: str, discovery_ts, exit_ts=None) -> pd.DataFrame:
        rows = [{"asset": symbol, "to_stage": LifecycleStage.DISCOVERY.value, "event_ts": discovery_ts}]
        if exit_ts is not None:
            rows.append({"asset": symbol, "to_stage": LifecycleStage.EXIT.value, "event_ts": exit_ts})
        return pd.DataFrame(rows)

    def test_lead_time_uses_peak_index_minus_entry_index(self):
        """
        全局扫描修复的核心回归测试：验证 peak_idx - entry_idx 这个索引
        算术在真实（非空、非退化）场景下算出的是正确的"时间步差"，而
        不是纯代码走读推断的结果。价格序列 index 0..9：
        [100,105,110,120,130,125,115,105,95,90]，峰值在 index=4（130）。
        入场在 index=1（DISCOVERY），离场在 index=7（EXIT）。
        预期 lead_time = peak_idx(4) - entry_idx(1) = 3。
        """
        closes = [100.0, 105.0, 110.0, 120.0, 130.0, 125.0, 115.0, 105.0, 95.0, 90.0]
        price_df = _make_price_df("TESTUSDT", closes)
        entry_ts = price_df["timestamp"].iloc[1]
        exit_ts = price_df["timestamp"].iloc[7]
        logs_df = self._make_logs_df("TESTUSDT", entry_ts, exit_ts)
        reporter = SimpleNamespace(logs_df=logs_df)

        lead_times, coverages, crashed = _epic_pool_cross_section(
            reporter, {"TEST": "TESTUSDT"}, price_df
        )

        assert lead_times == [3.0]
        # entry_price=105(index1), exit_price=105(index7), peak(窗口内)=130
        assert coverages == pytest.approx([0.0])

    def test_no_exit_yet_still_computes_lead_time_to_current_peak(self):
        """尚未触发 EXIT 时，窗口终点应该退回到数据末尾，而不是直接跳过整个资产"""
        closes = [100.0, 110.0, 150.0, 130.0, 120.0]
        price_df = _make_price_df("TESTUSDT", closes)
        entry_ts = price_df["timestamp"].iloc[0]
        logs_df = self._make_logs_df("TESTUSDT", entry_ts, exit_ts=None)
        reporter = SimpleNamespace(logs_df=logs_df)

        lead_times, coverages, crashed = _epic_pool_cross_section(
            reporter, {"TEST": "TESTUSDT"}, price_df
        )

        # 峰值在 index=2（150），入场 index=0 -> lead_time = 2
        assert lead_times == [2.0]

    def test_asset_without_any_state_transition_logs_is_skipped_not_errored(self):
        """完全没有状态迁移记录的资产应该被跳过，不应该抛异常或产生假样本"""
        price_df = _make_price_df("TESTUSDT", [100.0, 110.0])
        logs_df = pd.DataFrame(columns=["asset", "to_stage", "event_ts"])
        reporter = SimpleNamespace(logs_df=logs_df)

        lead_times, coverages, crashed = _epic_pool_cross_section(
            reporter, {"TEST": "TESTUSDT"}, price_df
        )

        assert lead_times == []
        assert coverages == []
        assert crashed == []

    def test_crashed_asset_detected_via_full_history_drawdown_not_window_drawdown(self):
        """
        崩溃资产判定用的是"全部历史"的峰值到最新价回撤（full_series），
        不是只看入场到离场窗口内的回撤——构造一个窗口内表现温和、但
        全历史看确实从峰值腰斩以上的资产，验证判定逻辑用的是正确的
        价格序列。
        """
        # 全历史峰值 200（index 2），最新价 50 -> 回撤 75% >= 50% 阈值
        closes = [100.0, 150.0, 200.0, 180.0, 160.0, 140.0, 100.0, 50.0]
        price_df = _make_price_df("CRASHUSDT", closes)
        entry_ts = price_df["timestamp"].iloc[0]
        exit_ts = price_df["timestamp"].iloc[3]  # 离场窗口早于真正崩盘发生
        logs_df = self._make_logs_df("CRASHUSDT", entry_ts, exit_ts)
        reporter = SimpleNamespace(logs_df=logs_df)

        lead_times, coverages, crashed = _epic_pool_cross_section(
            reporter, {"CRASH": "CRASHUSDT"}, price_df
        )

        assert len(crashed) == 1
        assert crashed[0][0] == "CRASH"

    def test_mild_asset_not_flagged_as_crashed(self):
        """全历史回撤低于阈值的资产不应该被误判为崩溃资产"""
        closes = [100.0, 110.0, 120.0, 115.0, 118.0]  # 回撤远小于 50%
        price_df = _make_price_df("MILDUSDT", closes)
        entry_ts = price_df["timestamp"].iloc[0]
        exit_ts = price_df["timestamp"].iloc[3]
        logs_df = self._make_logs_df("MILDUSDT", entry_ts, exit_ts)
        reporter = SimpleNamespace(logs_df=logs_df)

        _, _, crashed = _epic_pool_cross_section(reporter, {"MILD": "MILDUSDT"}, price_df)

        assert crashed == []
