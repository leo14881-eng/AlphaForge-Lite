"""
防洗盘量价背离逃顶逻辑的高压力仿真单测（v0.9 新增）。

这里只测"三层过滤网真实计算路径"（即真实喂入 close / delta2_rs /
crowding_penalty 序列，让 StateMachineEngine 自己算出
price_volume_divergent，而不是像 test_state_machine_engine.py 里那样
用显式覆盖值走捷径）——目的是端到端验证三层过滤网的实际判定效果：

    测试一 · 庄家恶性假砸盘洗盘：价格新高 + delta2_rs 断崖下穿零轴
        （过滤网一原始信号为真），但换手率/拥挤度全程没有连续触发
        高危报警（过滤网二不通过）——系统必须死守持仓，不被骗出场。

    测试二 · 类 LUNA / "币安人生" 式真实崩溃前夜：过滤网一、过滤网二
        均通过，且连续 2 个时间步都确认为真（过滤网三）——系统必须
        打破常规迟滞，前瞻性强制迁移至 EXIT。
"""
from state_machine.constants import LifecycleStage as Stage
from state_machine.engine import StateMachineEngine


def test_divergence_fake_washout_holds_running_position():
    """
    测试一：庄家恶性假砸盘洗盘期间，状态机必须死守持仓（LEADERSHIP）
    状态，不被"价格创新高但相对强度断崖"这一单一信号骗出场——因为
    换手率/拥挤度全程没有连续触发高危报警（过滤网二不通过），系统应
    判定为"高位缩量洗盘"，保持静默持股。
    """
    engine = StateMachineEngine()
    asset_id = "FAKEWASH"
    engine._current_stage_cache[asset_id] = Stage.LEADERSHIP
    engine._peak_score[asset_id] = 0.85

    closes = [100.0, 101.0, 102.0, 103.0]
    delta2_rs_values = [2.0, 1.0, -1.0, -2.0]  # 从正值断崖跌破零轴——过滤网一原始信号为真
    crowding_penalties = [0.9, 0.9, 0.9, 0.9]  # 全程不拥挤（阈值 0.5），缩量洗盘，过滤网二不通过

    results = []
    for close, delta2_rs, crowding_penalty in zip(closes, delta2_rs_values, crowding_penalties):
        result = engine.update_asset_state(
            asset_id,
            {
                "cs_score": 0.85,
                "close": close,
                "delta2_rs": delta2_rs,
                "crowding_penalty": crowding_penalty,
            },
            None,
        )
        results.append(result)

    # 全程都不应触发任何迁移，稳稳持有在 LEADERSHIP（RUNNING）
    assert results == [Stage.LEADERSHIP] * 4
    assert engine.last_transition is None


def test_divergence_real_breakdown_forces_exit_before_crash():
    """
    测试二：模拟类 LUNA / "币安人生"式的真实崩溃前夜——价格仍在创新高，
    但相对强度（delta2_rs）已经断崖式跌破零轴，且换手率连续触发拥挤度
    高危报警（过滤网一、过滤网二均通过），连续 2 个时间步确认（过滤网
    三）后，状态机必须打破常规迟滞，前瞻性强行切入 EXIT，而不是被动
    等到 CS 得分真的跌穿阈值——那时价格往往已经跌了一大截。
    """
    engine = StateMachineEngine()
    asset_id = "PRECRASH"
    engine._current_stage_cache[asset_id] = Stage.LEADERSHIP
    engine._peak_score[asset_id] = 0.85

    closes = [100.0, 101.0, 102.0, 103.0]
    delta2_rs_values = [2.0, 1.0, -1.0, -2.0]
    # 前两期不拥挤，第三、四期连续触发高危拥挤报警——让"拥挤度连续
    # 告警"与量价背离过滤网一的确认窗口在同一时间步（第 4 期）对齐，
    # 避免"纯拥挤度持续触发"这条独立机制在此之前抢先把资产降级到
    # DISTRIBUTION（详见 state_machine/engine.py::_compute_price_volume_divergent
    # 的实现注释）。
    crowding_penalties = [0.9, 0.9, 0.2, 0.2]

    results = []
    for close, delta2_rs, crowding_penalty in zip(closes, delta2_rs_values, crowding_penalties):
        result = engine.update_asset_state(
            asset_id,
            {
                "cs_score": 0.85,
                "close": close,
                "delta2_rs": delta2_rs,
                "crowding_penalty": crowding_penalty,
            },
            None,
        )
        results.append(result)

    # 前三期迟滞/确认窗口尚未走完，应保持 LEADERSHIP 不变
    assert results[:3] == [Stage.LEADERSHIP, Stage.LEADERSHIP, Stage.LEADERSHIP]
    # 第四期三层过滤全部确认，打破常规迟滞，前瞻性强制退出
    assert results[3] == Stage.EXIT
    assert engine.last_transition.from_stage == Stage.LEADERSHIP
    assert engine.last_transition.to_stage == Stage.EXIT
    assert "量价背离" in engine.last_transition.reason
