"""状态机常量模块的基础单元测试"""
from state_machine.constants import (
    ALLOWED_TRANSITIONS,
    STAGE_ORDER,
    LifecycleStage,
    is_transition_allowed,
)


def test_stage_order_covers_all_enum_members():
    assert set(STAGE_ORDER) == set(LifecycleStage)


def test_exit_is_terminal_state():
    assert ALLOWED_TRANSITIONS[LifecycleStage.EXIT] == frozenset()


def test_forward_transition_allowed():
    assert is_transition_allowed(LifecycleStage.SEED, LifecycleStage.DISCOVERY)


def test_illegal_jump_rejected():
    assert not is_transition_allowed(LifecycleStage.SEED, LifecycleStage.LEADERSHIP)
