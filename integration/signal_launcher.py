"""
信号弹射模块：Python 策略侧 -> Java 执行引擎（:8088）

背景：Java 侧（服务端）已实现 SignalController / SignalRequest DTO /
状态机引擎 / 多窗口共振审计字段（voting_windows_confirmed）；Python
策略侧（AlphaForge-Lite）此前只有纯计算/回测逻辑，没有任何对外输出
通道，两侧完全断开。本模块补上这最后一步——把 Python 侧算出的信号，
以 HTTP POST 的方式可靠送达 Java 侧。

**职责边界（务必先读）**：这是一个"信号搬运工"，只负责把信号可靠地
送到 Java 侧，不负责、也不应该负责判断这个信号本身该不该发——是否
触发信号，仍然由 state_machine.engine.StateMachineEngine 的三层过滤网
+ 多窗口共振投票决定，本模块不重新实现、也不能绕过那套判定逻辑。
同理，收到信号后是否真的执行清仓/建仓、以及执行侧的资金安全校验，
责任在 Java 侧的 SignalController，本模块只对"发送"这个动作本身的
健壮性（超时、异常隔离、日志）负责，不对下游交易安全性做任何背书。

**危险提醒（集成时务必注意）**：AlphaForge-Lite 的 Python 侧目前是纯
"本地静态历史数据回测"工具（见 backtest/runner.py），没有真实的实时
策略主循环。如果把 launch_signal() 直接接进 BacktestRunner 的批量回测
循环里，会导致每一次历史回放（包括 run_tuning.py / run_meme_stress_test.py
这类一次跑 12~18 组参数、逐行重放 2017-2026 年历史数据的批量脚本）都
把成千上万条"历史重放信号"当成真实信号弹射给 Java 执行引擎——如果
Java 侧真的据此下单，后果是灾难性的。本模块只应该被接入真实的"实时
策略主循环"（如果/当该主循环被实现），绝不能直接混进现有的批量回测
/参数寻优管线。详见 project_manifest.md 集成章节。

**生产安全锁**：launch() 入口处会先校验环境变量 ALPHA_RUN_MODE 是否
等于 LIVE，不是则直接返回 False、不发起任何网络请求。跟
live_monitor/market_monitor.py::SignalSink 用的是同一把锁、同一个环境
变量名，两处入口保持一致，防止新成员开发、本地单测、未来某个脚本
误 import 这个模块时不小心把信号真的发给 Java 执行引擎。

运行前需要安装：
    pip install requests

用法（策略主循环里的单行调用，仅用于实时/真实场景，不要用于回测；
只有显式设置 ALPHA_RUN_MODE=LIVE 才会真正发出网络请求）：
    from integration.signal_launcher import launch_signal
    launch_signal(asset="BTCUSDT", signal_type="EXIT",
                  confirmed_windows=2, total_windows=3)
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from typing import ClassVar

import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger("alphaforge.signal_launcher")

# 与 Java 侧 SignalRequest DTO 对齐：signalType 只允许这两个值
ALLOWED_SIGNAL_TYPES: frozenset[str] = frozenset({"DISCOVERY", "EXIT"})

# 生产安全锁：与 live_monitor/market_monitor.py 共用同一个环境变量名/值，
# 只有显式设置为 LIVE 才允许真正弹射信号
LIVE_MODE_ENV_VAR = "ALPHA_RUN_MODE"
LIVE_MODE_ENV_VALUE = "LIVE"


@dataclass(frozen=True)
class SignalLauncherConfig:
    """连接 Java 执行引擎的配置"""

    base_url: str = "http://127.0.0.1:8088"
    endpoint_path: str = "/api/v1/signals/trigger"
    timeout_seconds: float = 2.0  # 硬性红线：防止 Java 侧网络抖动拖死 Python 策略主线程
    max_retries: int = 1  # 超时/连接失败时的额外重试次数，默认只重试 1 次，避免拖慢主线程


class SignalLauncher:
    """
    HTTP 信号弹射器。

    基于 requests.Session 做连接池复用（避免每次调用都重新三次握手），
    通过 get_instance() 拿到进程内单例，线程安全（双重检查锁）。
    """

    _instance: ClassVar["SignalLauncher | None"] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, config: SignalLauncherConfig | None = None):
        self.config = config or SignalLauncherConfig()
        self._session = requests.Session()
        # max_retries=0：连接池适配器本身不重试，重试逻辑由 launch() 显式控制，
        # 避免和下面的手动重试叠加导致重试次数失控。
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    @classmethod
    def get_instance(cls, config: SignalLauncherConfig | None = None) -> "SignalLauncher":
        """进程内单例获取入口，双重检查锁保证多线程下只初始化一次"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(config)
        return cls._instance

    def launch(
        self,
        asset: str,
        signal_type: str,
        confirmed_windows: int | None = None,
        total_windows: int | None = None,
    ) -> bool:
        """
        发送一次信号弹射，返回是否发送成功；**永不向调用方抛出异常**——
        网络彻底断开、Java 引擎未启动、超时，都只记日志、返回 False，
        不能让 Python 策略主线程因为下游服务的问题而崩溃。

        入口第一道检查：生产安全锁。ALPHA_RUN_MODE 不等于 LIVE 时直接
        拒绝，不发起任何网络请求——防止误运行时把信号真的发给 Java。
        """
        if os.getenv(LIVE_MODE_ENV_VAR) != LIVE_MODE_ENV_VALUE:
            logger.warning(
                "[SignalLauncher] 当前不是 LIVE 模式（%s 未设置为 %s），已拒绝弹射（asset=%s "
                "signalType=%s），不发起任何网络请求",
                LIVE_MODE_ENV_VAR, LIVE_MODE_ENV_VALUE, asset, signal_type,
            )
            return False

        if signal_type not in ALLOWED_SIGNAL_TYPES:
            logger.error(
                "[SignalLauncher] 非法 signalType=%r，只允许 %s，已拒绝发送（asset=%s）",
                signal_type, sorted(ALLOWED_SIGNAL_TYPES), asset,
            )
            return False

        payload = {
            "asset": asset,
            "signalType": signal_type,
            "confirmedWindows": confirmed_windows,
            "totalWindows": total_windows,
        }
        url = f"{self.config.base_url}{self.config.endpoint_path}"
        attempts = self.config.max_retries + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                response = self._session.post(url, json=payload, timeout=self.config.timeout_seconds)
                if response.status_code >= 400:
                    logger.error(
                        "[SignalLauncher] Java 侧拒绝信号：asset=%s signalType=%s status=%s body=%s",
                        asset, signal_type, response.status_code, response.text[:500],
                    )
                    return False
                logger.info(
                    "[SignalLauncher] 信号弹射成功：asset=%s signalType=%s "
                    "confirmedWindows=%s/%s status=%s",
                    asset, signal_type, confirmed_windows, total_windows, response.status_code,
                )
                return True
            except requests.exceptions.Timeout as exc:
                last_error = exc
                logger.warning(
                    "[SignalLauncher] 第 %d/%d 次尝试超时（>%.1fs）：asset=%s signalType=%s",
                    attempt, attempts, self.config.timeout_seconds, asset, signal_type,
                )
            except requests.exceptions.ConnectionError as exc:
                last_error = exc
                logger.warning(
                    "[SignalLauncher] 第 %d/%d 次尝试连接失败"
                    "（Java 引擎可能未启动/网络不可达）：asset=%s signalType=%s error=%s",
                    attempt, attempts, asset, signal_type, exc,
                )
            except Exception as exc:  # 兜底：任何未预料异常都不能向上抛出，否则策略主线程会崩
                last_error = exc
                logger.exception(
                    "[SignalLauncher] 弹射过程发生未预期异常：asset=%s signalType=%s",
                    asset, signal_type,
                )
                break  # 未知异常不重试，直接失败退出，避免在未知错误上反复重试拖慢主线程

        logger.error(
            "[SignalLauncher] 信号弹射最终失败（已尝试 %d 次）：asset=%s signalType=%s 最后错误=%s",
            attempts, asset, signal_type, last_error,
        )
        return False


def launch_signal(
    asset: str,
    signal_type: str,
    confirmed_windows: int | None = None,
    total_windows: int | None = None,
) -> bool:
    """
    模块级便捷函数：策略主循环里一行调用即可，内部使用进程内单例
    SignalLauncher，调用方不需要自己管理连接池/session 生命周期。
    """
    return SignalLauncher.get_instance().launch(asset, signal_type, confirmed_windows, total_windows)
