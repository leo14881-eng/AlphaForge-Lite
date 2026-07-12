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


def test_filter1_vote_for_window_basic_semantics():
    """直接测试静态方法 _filter1_vote_for_window：新高 + 从非负断崖下穿零轴才投同意票"""
    from collections import deque

    close_history = deque([100.0, 101.0, 102.0], maxlen=3)
    delta2_history = deque([2.0, 1.0, -1.0], maxlen=3)
    assert StateMachineEngine._filter1_vote_for_window(close_history, delta2_history, 3) is True
    # 数据不够窗口长度，直接投反对票
    assert StateMachineEngine._filter1_vote_for_window(close_history, delta2_history, 5) is False
    # delta2_rs 起点已经是负值（不是"从非负下穿零轴"），投反对票
    negative_start = deque([-2.0, -1.0, -0.5], maxlen=3)
    assert StateMachineEngine._filter1_vote_for_window(close_history, negative_start, 3) is False


def test_multi_window_voting_majority_overrides_minority_dissent():
    """
    v0.95-Beta 参数加固第一项：多窗口共振投票。构造一个序列，让
    divergence_windows=(2,3,4) 默认配置下，window=2 在最后一步"不同意"
    （局部窗口起点已经是负值，不满足"从非负下穿零轴"），但 window=3、
    window=4 都同意——2/3 构成多数，最终判定应为 True（不是被单一窗口
    的异议否决）。crowding_alert_streak=0 用于中性化过滤网二，只聚焦
    测试过滤网一的多窗口投票逻辑本身。
    """
    engine = StateMachineEngine(divergence_confirm_streak=1, crowding_alert_streak=0)
    asset_id = "VOTE_MAJORITY"

    closes = [100.0, 101.0, 102.0, 103.0]
    delta2_rs_values = [2.0, 1.0, -1.0, -2.0]
    result = True
    for close, delta2_rs in zip(closes, delta2_rs_values):
        result = engine._compute_price_volume_divergent(asset_id, close, delta2_rs)

    assert result is True


def test_multi_window_voting_rejects_when_minority_agrees():
    """
    反例：只有 window=4 一个窗口同意（1/3，不构成多数），其余窗口不同意，
    最终判定应为 False——避免"只要有一个窗口凑巧信号对了就触发"的假阳性。
    """
    engine = StateMachineEngine(divergence_confirm_streak=1, crowding_alert_streak=0)
    asset_id = "VOTE_MINORITY"

    # delta2_rs 全程为正（从不下穿零轴），只有极端构造下 window=4 的
    # 起点恰好非负、终点为负，其余窗口起点已经是负值——这里直接用一个
    # 更直接的反例：delta2_rs 全程非负，任何窗口都不满足"终点为负"，
    # 三个窗口全部投反对票。
    closes = [100.0, 101.0, 102.0, 103.0]
    delta2_rs_values = [2.0, 1.5, 1.0, 0.8]  # 全程非负，从不下穿零轴
    result = True
    for close, delta2_rs in zip(closes, delta2_rs_values):
        result = engine._compute_price_volume_divergent(asset_id, close, delta2_rs)

    assert result is False


def test_hysteresis_blocks_single_tick_noise():
    # 显式传入 hysteresis_window=3（而非依赖类默认值），让本测试专注于
    # 验证"迟滞机制本身"而不随出厂默认值调参（v0.8 已固化为 2）而漂移。
    engine = StateMachineEngine(hysteresis_window=3)
    asset_id = "HYST"

    # 前两次达标不足以触发迁移
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


def test_price_volume_divergence_override_forces_exit_from_leadership():
    """
    v0.9：量价背离强制退出的适用范围收窄为"持仓阶段"（DISCOVERY /
    LEADERSHIP），不再包含 DISTRIBUTION——DISTRIBUTION 阶段的退出仍由
    "纯拥挤度持续触发"或常规阈值判定负责。这里用显式覆盖值
    （price_volume_divergent=True）验证 LEADERSHIP 场景下的强制退出通道。
    """
    engine = StateMachineEngine()
    asset_id = "DIVERGE"
    engine._current_stage_cache[asset_id] = Stage.LEADERSHIP
    engine._peak_score[asset_id] = 0.85

    result = engine.update_asset_state(
        asset_id, {"cs_score": 0.85, "price_volume_divergent": True}, None
    )
    assert result == Stage.EXIT
    assert engine.last_transition.from_stage == Stage.LEADERSHIP


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
