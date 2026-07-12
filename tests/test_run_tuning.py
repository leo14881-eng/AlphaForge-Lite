"""run_tuning.py 参数网格扫描脚本的单元测试"""
import numpy as np
import pandas as pd
import pytest

import run_tuning
from database.models import MODELS
from database.models import db as peewee_db


def _make_synthetic_csv(path) -> None:
    rng = np.random.default_rng(21)
    n = 120
    ts = pd.date_range("2026-01-01", periods=n, freq="D")
    volume = rng.normal(1000, 30, n) * np.concatenate([np.ones(60), np.linspace(1.3, 2.2, 60)])
    close = 100 * np.cumprod(1 + rng.normal(0.0025, 0.008, n))
    df = pd.DataFrame(
        {
            "timestamp": ts,
            "symbol": "TUNEDEMO",
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


def test_weight_pairs_sum_to_one():
    for w_a, w_b in run_tuning.WEIGHT_PAIRS:
        assert w_a + w_b == pytest.approx(1.0)


def test_weight_pairs_and_hysteresis_windows_match_spec():
    assert run_tuning.WEIGHT_PAIRS == ((0.8, 0.2), (0.6, 0.4), (0.4, 0.6), (0.2, 0.8))
    assert run_tuning.HYSTERESIS_WINDOWS == (2, 3, 4)


def test_run_one_combo_produces_valid_result(tmp_path, memory_db):
    csv_path = tmp_path / "tuning_wide_table.csv"
    _make_synthetic_csv(csv_path)
    data = pd.read_csv(csv_path)
    data["timestamp"] = pd.to_datetime(data["timestamp"])

    result = run_tuning._run_one_combo(data, str(csv_path), w_a=0.6, w_b=0.4, hysteresis_window=2)

    assert result.run_id
    assert result.w_a == 0.6
    assert result.w_b == 0.4
    assert result.hysteresis_window == 2
    assert result.seed_trigger_count >= 0


def test_leaderboard_prints_ranking_and_conclusion(tmp_path, memory_db, capsys):
    csv_path = tmp_path / "tuning_wide_table.csv"
    _make_synthetic_csv(csv_path)
    data = pd.read_csv(csv_path)
    data["timestamp"] = pd.to_datetime(data["timestamp"])

    results = [
        run_tuning._run_one_combo(data, str(csv_path), w_a=0.8, w_b=0.2, hysteresis_window=2),
        run_tuning._run_one_combo(data, str(csv_path), w_a=0.2, w_b=0.8, hysteresis_window=3),
    ]
    run_tuning._print_leaderboard(results)

    captured = capsys.readouterr()
    assert "参数审计天梯榜" in captured.out
    assert "结论" in captured.out
