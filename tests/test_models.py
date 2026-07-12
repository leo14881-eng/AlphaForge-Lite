"""数据库模型的基础单元测试，使用内存 SQLite 库，不依赖本地文件"""
import datetime as dt

import peewee as pw
import pytest

from database.models import MODELS, Asset, BacktestRun, StateTransitionLog
from state_machine.constants import LifecycleStage

_memory_db = pw.SqliteDatabase(":memory:")


@pytest.fixture()
def bound_db():
    """将模型临时绑定到内存库，测试结束后自动解绑，不污染真实数据库文件"""
    with _memory_db.bind_ctx(MODELS):
        _memory_db.create_tables(MODELS)
        yield _memory_db
        _memory_db.drop_tables(MODELS)


def test_insert_asset_and_transition_log(bound_db):
    asset = Asset.create(symbol="BTCUSDT", first_seen_at=dt.datetime.utcnow())

    log = StateTransitionLog.create(
        asset=asset,
        event_ts=dt.datetime.utcnow(),
        from_stage=None,
        to_stage=LifecycleStage.SEED.value,
        cs_score=0.35,
        trigger_reason="首次识别到非共识资金介入信号",
    )
    log.set_component_breakdown(
        {"volume_anomaly": 0.2, "holder_concentration": 0.1, "capital_inflow": 0.05}
    )
    log.save()

    saved = StateTransitionLog.get_by_id(log.id)
    assert saved.to_stage == LifecycleStage.SEED.value
    assert saved.asset.symbol == "BTCUSDT"
    assert saved.get_component_breakdown()["holder_concentration"] == 0.1


def test_transition_log_can_bind_to_backtest_run(bound_db):
    asset = Asset.create(symbol="ETHUSDT", first_seen_at=dt.datetime.utcnow())
    run = BacktestRun.create(
        strategy_name="non_consensus_accumulation",
        strategy_version="v0.1",
        data_start_ts=dt.datetime(2026, 1, 1),
        data_end_ts=dt.datetime(2026, 6, 30),
    )

    log = StateTransitionLog.create(
        asset=asset,
        backtest_run=run,
        event_ts=dt.datetime(2026, 3, 1),
        from_stage=LifecycleStage.SEED.value,
        to_stage=LifecycleStage.DISCOVERY.value,
        cs_score=0.51,
    )

    assert log.backtest_run.strategy_name == "non_consensus_accumulation"
    assert run.transitions.count() == 1
