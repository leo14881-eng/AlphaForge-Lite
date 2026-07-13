"""
端到端集成测试：DataLoader -> BacktestRunner -> BacktestReporter

构造一份包含"温和放量 + 持续上涨 + 后期拥挤反转"模式的合成大宽表，
验证完整回测主流程能够正确运行、落库，并且复盘报表三块看板都能
正常生成，不对具体命中的阶段做过强断言（探测器/状态机的具体阈值
属于可调超参数，这里只验证管线本身的正确性与健壮性）。
"""
import numpy as np
import pandas as pd
import peewee as pw
import pytest

from backtest.data_loader import DataLoader
from backtest.report import BacktestReporter
from backtest.runner import BacktestConfig, BacktestRunner
from database.models import MODELS
from database.models import db as peewee_db
from state_machine.constants import STAGE_ORDER


def _make_synthetic_csv(path) -> None:
    rng = np.random.default_rng(7)
    n = 150
    timestamps = pd.date_range("2026-01-01", periods=n, freq="D")
    frames = []

    # PUMP：先平静，再温和放量 + 持续上涨，最后拥挤反转
    close = np.empty(n)
    volume = np.empty(n)
    close[0] = 100.0
    volume[:60] = rng.normal(1000, 20, size=60)
    volume[60:110] = rng.normal(1000, 20, size=50) * np.linspace(1.3, 2.3, 50)
    volume[110:] = rng.normal(1000, 20, size=n - 110) * 0.8
    for i in range(1, n):
        if i < 60:
            drift = 0.0005
        elif i < 110:
            drift = 0.02
        else:
            drift = -0.015
        close[i] = close[i - 1] * (1 + drift + rng.normal(0, 0.004))
    turnover = rng.uniform(0.01, 0.02, size=n)
    turnover[110:130] *= 6  # 后期换手率极端拥挤
    funding = rng.normal(0.0001, 0.00003, size=n)
    funding[110:130] += 0.01  # 资金费率同步走高
    frames.append(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "symbol": "PUMP",
                "close": close,
                "volume": volume,
                "turnover_rate": turnover,
                "funding_rate": funding,
            }
        )
    )

    # FLAT：全程无结构性信号
    flat_close = 50 * np.cumprod(1 + rng.normal(0, 0.003, size=n))
    frames.append(
        pd.DataFrame(
            {
                "timestamp": timestamps,
                "symbol": "FLAT",
                "close": flat_close,
                "volume": rng.normal(800, 15, size=n),
                "turnover_rate": rng.uniform(0.008, 0.015, size=n),
                "funding_rate": rng.normal(0.0001, 0.00003, size=n),
            }
        )
    )

    pd.concat(frames, ignore_index=True).to_csv(path, index=False)


@pytest.fixture()
def memory_db():
    peewee_db.init(":memory:")
    peewee_db.connect()
    peewee_db.create_tables(MODELS)
    yield peewee_db
    peewee_db.drop_tables(MODELS)
    peewee_db.close()


def test_end_to_end_pipeline_runs_and_reports(tmp_path, memory_db, capsys):
    csv_path = tmp_path / "synthetic_wide_table.csv"
    _make_synthetic_csv(csv_path)

    df = DataLoader().load_path(csv_path)
    assert set(df["symbol"].unique()) == {"PUMP", "FLAT"}

    config = BacktestConfig(
        strategy_name="non_consensus_accumulation",
        strategy_version="test",
        data_source=str(csv_path),
        symbols=["PUMP", "FLAT"],
    )
    runner = BacktestRunner(data=df, config=config)
    run_row = runner.run()

    assert run_row.status == "SUCCESS"
    assert run_row.run_id

    from database.models import StateTransitionLog

    total_logs = StateTransitionLog.select().count()
    assert total_logs > 0

    reporter = BacktestReporter(run_id=run_row.run_id)

    duration_report = reporter.stage_duration_report()
    assert list(duration_report["stage"]) == [s.value for s in STAGE_ORDER]

    path_dist = reporter.transition_path_distribution()
    assert len(path_dist) > 0

    attribution = reporter.non_consensus_attribution()
    assert attribution.empty or {"component", "avg_score", "contribution_share"}.issubset(attribution.columns)

    lead_time_df = reporter.lead_time_audit()
    if not lead_time_df.empty:
        assert "lead_time_bars" in lead_time_df.columns
        assert lead_time_df["lead_time_bars"].dtype.kind in "iu"

    reporter.print_report()
    captured = capsys.readouterr()
    assert "AlphaForge-Lite 回测复盘报告" in captured.out
    assert run_row.run_id in captured.out


def test_runner_rejects_empty_data_after_symbol_filter(tmp_path, memory_db):
    """
    全局扫描修复：config.symbols 过滤后数据为空时，应该在构造阶段就
    显式拒绝（ValueError），不能让空表一路传到 run() 里——
    self.data["timestamp"].min() 在空表上会静默返回 NaT，NaT 会被传给
    BacktestRun.create(data_start_ts=...) 落库，这是一个本该在配置校验
    阶段就被拒绝的错误配置。
    """
    csv_path = tmp_path / "synthetic_wide_table.csv"
    _make_synthetic_csv(csv_path)
    df = DataLoader().load_path(csv_path)

    config = BacktestConfig(
        strategy_name="non_consensus_accumulation",
        strategy_version="test",
        data_source=str(csv_path),
        symbols=["THIS_SYMBOL_DOES_NOT_EXIST"],
    )

    with pytest.raises(ValueError, match="过滤后数据集为空"):
        BacktestRunner(data=df, config=config)
