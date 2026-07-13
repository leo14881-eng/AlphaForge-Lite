"""integration/signal_launcher.py 的单元测试（全程 mock 网络层，不依赖真实 Java 服务）"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from integration.signal_launcher import SignalLauncher, SignalLauncherConfig, launch_signal


@pytest.fixture()
def launcher():
    config = SignalLauncherConfig(base_url="http://127.0.0.1:8088", timeout_seconds=2.0, max_retries=1)
    return SignalLauncher(config)


@pytest.fixture(autouse=True)
def reset_singleton():
    """每个测试前后清空进程内单例，避免测试间状态串扰"""
    SignalLauncher._instance = None
    yield
    SignalLauncher._instance = None


@pytest.fixture(autouse=True)
def live_mode(monkeypatch):
    """
    默认给所有测试开启 LIVE 模式，模拟"已经过安全确认的真实生产环境"，
    这样下面测试重试/超时/成功路径的用例不会被新加的生产安全锁挡在
    最前面。专门测试安全锁本身的用例会自行覆盖/清除这个环境变量。
    """
    monkeypatch.setenv("ALPHA_RUN_MODE", "LIVE")


def test_launch_success_sends_correct_payload(launcher):
    mock_response = MagicMock(status_code=200, text="OK")
    with patch.object(launcher._session, "post", return_value=mock_response) as mock_post:
        result = launcher.launch("BTCUSDT", "EXIT", confirmed_windows=2, total_windows=3)

    assert result is True
    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["json"] == {
        "asset": "BTCUSDT",
        "signalType": "EXIT",
        "confirmedWindows": 2,
        "totalWindows": 3,
    }
    assert kwargs["timeout"] == 2.0


def test_launch_rejects_invalid_signal_type_without_network_call(launcher):
    with patch.object(launcher._session, "post") as mock_post:
        result = launcher.launch("BTCUSDT", "PUMP_TO_MOON")

    assert result is False
    mock_post.assert_not_called()


def test_launch_returns_false_on_http_error_status_no_retry(launcher):
    mock_response = MagicMock(status_code=500, text="internal error")
    with patch.object(launcher._session, "post", return_value=mock_response) as mock_post:
        result = launcher.launch("BTCUSDT", "DISCOVERY")

    assert result is False
    mock_post.assert_called_once()  # 4xx/5xx 不重试


def test_launch_retries_once_on_timeout_then_fails(launcher):
    with patch.object(launcher._session, "post", side_effect=requests.exceptions.Timeout("timed out")) as mock_post:
        result = launcher.launch("BTCUSDT", "EXIT")

    assert result is False
    assert mock_post.call_count == 2  # max_retries=1 -> 共尝试 2 次


def test_launch_retries_once_on_connection_error_then_fails(launcher):
    with patch.object(
        launcher._session, "post", side_effect=requests.exceptions.ConnectionError("refused")
    ) as mock_post:
        result = launcher.launch("BTCUSDT", "EXIT")

    assert result is False
    assert mock_post.call_count == 2


def test_launch_succeeds_on_second_attempt_after_first_timeout(launcher):
    mock_response = MagicMock(status_code=200, text="OK")
    with patch.object(
        launcher._session, "post", side_effect=[requests.exceptions.Timeout("timed out"), mock_response]
    ) as mock_post:
        result = launcher.launch("BTCUSDT", "EXIT")

    assert result is True
    assert mock_post.call_count == 2


def test_launch_unexpected_exception_does_not_propagate_and_does_not_retry(launcher):
    with patch.object(launcher._session, "post", side_effect=ValueError("boom")) as mock_post:
        result = launcher.launch("BTCUSDT", "EXIT")  # 不应该抛出异常

    assert result is False
    mock_post.assert_called_once()  # 未知异常不重试


def test_launch_blocked_when_run_mode_env_missing(launcher, monkeypatch):
    monkeypatch.delenv("ALPHA_RUN_MODE", raising=False)
    with patch.object(launcher._session, "post") as mock_post:
        result = launcher.launch("BTCUSDT", "EXIT")

    assert result is False
    mock_post.assert_not_called()  # 安全锁在最前面拦下，根本不该发起网络请求


def test_launch_blocked_when_run_mode_env_has_wrong_value(launcher, monkeypatch):
    monkeypatch.setenv("ALPHA_RUN_MODE", "BACKTEST")
    with patch.object(launcher._session, "post") as mock_post:
        result = launcher.launch("BTCUSDT", "EXIT")

    assert result is False
    mock_post.assert_not_called()


def test_get_instance_returns_singleton():
    first = SignalLauncher.get_instance()
    second = SignalLauncher.get_instance()
    assert first is second


def test_launch_signal_module_function_uses_singleton():
    mock_response = MagicMock(status_code=200, text="OK")
    instance = SignalLauncher.get_instance()
    with patch.object(instance._session, "post", return_value=mock_response) as mock_post:
        result = launch_signal("ETHUSDT", "DISCOVERY", confirmed_windows=3, total_windows=3)

    assert result is True
    mock_post.assert_called_once()
