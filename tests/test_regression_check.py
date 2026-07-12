"""run_regression_check.py 无损对比回归脚本的单元测试"""
import numpy as np
import pandas as pd
import pytest

import run_regression_check
from database.models import MODELS
from database.models import db as peewee_db
from detectors.cs_score import CCSDetector
from state_machine.engine import StateMachineEngine


def _make_synthetic_csv(path) -> None:
    rng = np.random.default_rng(31)
    n = 120
    ts = pd.date_range("2026-01-01", periods=n, freq="D")
    volume = rng.normal(1000, 30, n) * np.concatenate([np.ones(60), np.linspace(1.3, 2.2, 60)])
    close = 100 * np.cumprod(1 + rng.normal(0.0025, 0.008, n))
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": "REGDEMO",
            "close": close,
            "volume": volume,
            "turnover_rate": rng.uniform(0.01, 0.02, n),
            "funding_rate": rng.normal(0.0001, 0.00003, n),
        }
    )
    df.to_csv(path, index=False)


@pytest.fixture()
def memory_db():
    peewee_db.init(":memory:")
    peewee_db.connect()
    peewee_db.create_tables(MODELS)
    yield peewee_db
    peewee_db.drop_tables(MODELS)
    peewee_db.close()


def test_default_constructors_reflect_calibrated_values():
    """回填校验：确认源码默认值确实是 v0.8 固化后的实测最优参数"""
    detector = CCSDetector()
    engine = StateMachineEngine()
    assert detector.weight_delta2_rs == 0.8
    assert detector.weight_volume_delta == 0.2
    assert engine.hysteresis_window == 2


def test_run_experiment_explicit_params_used_as_is(tmp_path, memory_db):
    csv_path = tmp_path / "regression_wide_table.csv"
    _make_synthetic_csv(csv_path)
    data = pd.read_csv(csv_path)
    data["timestamp"] = pd.to_datetime(data["timestamp"])

    result = run_regression_check._run_experiment(
        data, str(csv_path), "A: 旧启发式默认", w_a=0.5, w_b=0.5, hysteresis_window=3
    )
    assert result.w_a == 0.5
    assert result.w_b == 0.5
    assert result.hysteresis_window == 3
    assert result.run_id


def test_run_experiment_none_params_use_class_defaults(tmp_path, memory_db):
    csv_path = tmp_path / "regression_wide_table.csv"
    _make_synthetic_csv(csv_path)
    data = pd.read_csv(csv_path)
    data["timestamp"] = pd.to_datetime(data["timestamp"])

    result = run_regression_check._run_experiment(
        data, str(csv_path), "B: 新固化默认", w_a=None, w_b=None, hysteresis_window=None
    )
    assert result.w_a == CCSDetector().weight_delta2_rs
    assert result.w_b == CCSDetector().weight_volume_delta
    assert result.hysteresis_window == StateMachineEngine().hysteresis_window


def test_scoreboard_prints_comparison_and_conclusion(tmp_path, memory_db, capsys):
    csv_path = tmp_path / "regression_wide_table.csv"
    _make_synthetic_csv(csv_path)
    data = pd.read_csv(csv_path)
    data["timestamp"] = pd.to_datetime(data["timestamp"])

    result_a = run_regression_check._run_experiment(
        data, str(csv_path), "A: 旧启发式默认", 0.5, 0.5, 3
    )
    result_b = run_regression_check._run_experiment(
        data, str(csv_path), "B: 新固化默认", None, None, None
    )
    run_regression_check._print_scoreboard(result_a, result_b)

    captured = capsys.readouterr()
    assert "无损对比大考成绩" in captured.out
    assert "结论" in captured.out
