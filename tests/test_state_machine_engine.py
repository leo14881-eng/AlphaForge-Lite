"""StateMachineEngine（自适应状态机执行引擎）的基础单元测试"""
from state_machine.constants import LifecycleStage as Stage
from state_machine.engine import StateMachineEngine


class _FakeStageLookup:
    """模拟 db_session：记录调用次数，返回预设的历史阶段"""

    def __init__(self, stage_value: str | None):
        self.stage_value = stage_value
        self.calls = 0

    def get_last_stage(self, asset_id: str) -> str | None:
        self.calls += 1
        return self.stage_value


def test_hysteresis_blocks_single_tick_noise():
    engine = StateMachineEngine()
    asset_id = "HYST"

    # 前两次达标不足以触发迁移（默认 hysteresis_window=3）
    assert engine.update_asset_state(asset_id, {"cs_score": 0.5}, None) is None
    assert engine.update_asset_state(asset_id, {"cs_score": 0.5}, None) is None
    # 第三次连续达标才真正进入 SEED
    result = engine.update_asset_state(asset_id, {"cs_score": 0.5}, None)
    assert result == Stage.SEED
    assert engine.last_transition.from_stage is None
    assert engine.last_transition.to_stage == Stage.SEED


def test_volatility_adaptive_threshold_scales_up_and_down():
    engine = StateMachineEngine()
    asset_id = "VOLX"

    # 高波动率（裁剪至 vol_scale_max=1.5）下，0.5 分不足以越过 0.35*1.5=0.525
    for _ in range(3):
        assert engine.update_asset_state(asset_id, {"cs_score": 0.5, "market_atr_ratio": 3.0}, None) is None

    # 波动率回落到正常水平后，同样的分数应立刻确认进入 SEED
    result = engine.update_asset_state(asset_id, {"cs_score": 0.5, "market_atr_ratio": 1.0}, None)
    assert result == Stage.SEED


def test_forward_looking_exit_on_persistent_crowding():
    engine = StateMachineEngine()
    asset_id = "CROWD"
    engine._current_stage_cache[asset_id] = Stage.LEADERSHIP
    engine._peak_score[asset_id] = 0.9

    # 第一次拥挤：连续计数未达 crowding_alert_streak(2)，维持 LEADERSHIP
    first = engine.update_asset_state(asset_id, {"cs_score": 0.85, "crowding_penalty": 0.3}, None)
    assert first == Stage.LEADERSHIP
    assert engine.last_transition is None

    # 第二次连续拥挤：即便 CS 得分仍处高位（未触发常规回撤退出），也应提前切入 DISTRIBUTION
    second = engine.update_asset_state(asset_id, {"cs_score": 0.85, "crowding_penalty": 0.3}, None)
    assert second == Stage.DISTRIBUTION
    assert engine.last_transition.from_stage == Stage.LEADERSHIP
    assert "拥挤" in engine.last_transition.reason


def test_price_volume_divergence_forces_exit_from_distribution():
    engine = StateMachineEngine()
    asset_id = "DIVERGE"
    engine._current_stage_cache[asset_id] = Stage.DISTRIBUTION

    result = engine.update_asset_state(
        asset_id, {"cs_score": 0.5, "price_volume_divergent": True}, None
    )
    assert result == Stage.EXIT


def test_full_lifecycle_progression_is_always_legal():
    engine = StateMachineEngine(hysteresis_window=1)
    asset_id = "PROG"

    sequence = [
        (0.5, Stage.SEED),
        (0.55, Stage.DISCOVERY),
        (0.7, Stage.CONFIRMATION),
        (0.85, Stage.LEADERSHIP),
        (0.6, Stage.DISTRIBUTION),  # 相对峰值 0.85 回撤 29% > 15%
        (0.2, Stage.EXIT),  # 跌破 exit_floor=0.3
    ]
    for score, expected_stage in sequence:
        result = engine.update_asset_state(asset_id, {"cs_score": score}, None)
        assert result == expected_stage

    # EXIT 后应重置为新的观察周期，同样的得分可以重新进入 SEED
    restarted = engine.update_asset_state(asset_id, {"cs_score": 0.5}, None)
    assert restarted == Stage.SEED


def test_db_session_resolves_and_caches_last_stage():
    engine = StateMachineEngine()
    lookup = _FakeStageLookup(Stage.DISCOVERY.value)

    result = engine.update_asset_state("RESUME", {"cs_score": 0.5}, lookup)
    assert result == Stage.DISCOVERY
    assert lookup.calls == 1

    # 命中本地缓存后不应再次查询 db_session
    result_again = engine.update_asset_state("RESUME", {"cs_score": 0.5}, lookup)
    assert result_again == Stage.DISCOVERY
    assert lookup.calls == 1


def test_db_session_exit_collapses_to_fresh_cycle():
    engine = StateMachineEngine()
    lookup = _FakeStageLookup(Stage.EXIT.value)

    # 历史最后阶段是 EXIT，应等价于"无历史"，得分不足以立刻确认 SEED
    result = engine.update_asset_state("RESTARTED", {"cs_score": 0.1}, lookup)
    assert result is None
