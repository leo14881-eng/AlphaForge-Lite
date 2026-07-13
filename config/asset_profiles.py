"""
资产画像字典（v0.95-Beta 新增，参数加固第二项）

拒绝用一组参数通吃：`run_tuning.py`（优化"尽早发现"）在 12 个主流资产上
跑出的实测最优权重是 w_a=0.8/w_b=0.2；`run_meme_stress_test.py`（优化
"安全逃顶"）在 15 个妖币资产上跑出的实测最优权重是 w_a=0.05/w_b=0.95——
两者方向几乎相反（详见 project_manifest.md 诚实声明第 2 点）。本模块把
这两组实测结果按资产类别固化下来，供 `BacktestConfig` 的默认生产路径
按资产分类自动挂载对应权重，而不是全局共用一组参数。

用法边界说明：`run_tuning.py` / `run_regression_check.py` /
`run_meme_stress_test.py` 这三个参数寻优/压力测试工具会继续显式构造
自己的 `CCSDetector(weight_delta2_rs=..., weight_volume_delta=...)`，
不使用本模块的按资产覆盖机制——它们的研究目的就是测试"同一组权重在
一批资产上的整体表现"，如果也套用分类覆盖，反而会让参数寻优的结果
失去意义。本模块只影响 `main.py`/`api/app.py` 走的默认生产路径
（`BacktestConfig()` 不显式传入自定义 detector 时）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AssetClass(str, Enum):
    """资产类别：主流稳健资产 vs 妖币/神币（高爆发、剧烈洗盘、可能归零）"""

    CORE = "CORE"
    MEME = "MEME"


@dataclass(frozen=True)
class AssetClassProfile:
    """一个资产类别对应的实测最优 CCS 权重对"""

    weight_delta2_rs: float  # w_a：组件A（相对强度斜率加速度）权重
    weight_volume_delta: float  # w_b：组件B（温和放量）权重


# CORE：run_tuning.py 在 12 个主流资产上跑出的实测最优（"尽早发现"目标）
# MEME：run_meme_stress_test.py 在 15 个妖币资产上跑出的实测最优（"安全逃顶"目标）
ASSET_CLASS_PROFILES: dict[AssetClass, AssetClassProfile] = {
    AssetClass.CORE: AssetClassProfile(weight_delta2_rs=0.8, weight_volume_delta=0.2),
    AssetClass.MEME: AssetClassProfile(weight_delta2_rs=0.05, weight_volume_delta=0.95),
}

# 【全局扫描修复：单一权威来源】12 个主流稳健资产的规范清单（不带 "/"
# 的内部格式，如 "BTCUSDT"）。此前这份清单在 data/download_data.py /
# run_tuning.py / run_regression_check.py / 本文件的 ASSET_PROFILE_MAP
# 四处各自独立硬编码——fork 扫描时写脚本验证过四处目前恰好一致，但没有
# 任何机制保证以后改了一处会同步其余三处，一旦漂移会静默产生"历史结果
# 不可复现"的问题。现在这里是唯一权威来源：run_tuning.py/
# run_regression_check.py 直接从本模块 import；data/download_data.py
# 需要 ccxt 的 "BTC/USDT" 斜杠格式，用 _to_ccxt_symbol() 转换后使用。
MAINSTREAM_SYMBOLS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "ADAUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "LTCUSDT",
    "TRXUSDT",
)


def _to_ccxt_symbol(symbol: str) -> str:
    """'BTCUSDT' -> 'BTC/USDT'（ccxt 现货交易对格式），仅供内部/
    data/download_data.py 转换 MAINSTREAM_SYMBOLS 使用。"""
    assert symbol.endswith("USDT"), f"预期以 USDT 结尾的规范格式，收到: {symbol}"
    return f"{symbol[:-4]}/USDT"


# ccxt 格式（"BTC/USDT"）的主流资产清单，供 data/download_data.py 直接用，
# 不需要自己再维护一份独立硬编码的列表。
MAINSTREAM_SYMBOLS_CCXT: tuple[str, ...] = tuple(_to_ccxt_symbol(s) for s in MAINSTREAM_SYMBOLS)

# 26 个资产的显式分类，与 data/download_data.py 的 MAINSTREAM_SYMBOLS /
# EPIC_POOL_SYMBOLS 保持一致。SOL 在两份下载清单里都出现过（EPIC_POOL
# 为了做妖币池截面对照把它也纳入了），但 SOL 本身是市值靠前的蓝筹资产，
# 不具备典型妖币特征，这里按真实资产属性判定为 CORE——这是一个刻意的
# 判断，不是照抄下载清单的归属。
ASSET_PROFILE_MAP: dict[str, AssetClass] = {
    **{symbol: AssetClass.CORE for symbol in MAINSTREAM_SYMBOLS},
    "GALAUSDT": AssetClass.MEME,
    "AXSUSDT": AssetClass.MEME,
    "WIFUSDT": AssetClass.MEME,
    "FLOKIUSDT": AssetClass.MEME,
    "LUNAUSDT": AssetClass.MEME,
    "BONKUSDT": AssetClass.MEME,
    "PEPEUSDT": AssetClass.MEME,
    "TIAUSDT": AssetClass.MEME,
    "SUIUSDT": AssetClass.MEME,
    "币安人生USDT": AssetClass.MEME,
    "ACTUSDT": AssetClass.MEME,
    "GOATUSDT": AssetClass.MEME,
    "PNUTUSDT": AssetClass.MEME,
    "MOODENGUSDT": AssetClass.MEME,
}


def build_asset_weight_overrides() -> dict[str, tuple[float, float]]:
    """
    把 ASSET_PROFILE_MAP 展开成 {symbol: (w_a, w_b)}，供
    detectors.cs_score.CCSDetector(asset_weight_overrides=...) 直接使用。
    """
    return {
        symbol: (
            ASSET_CLASS_PROFILES[asset_class].weight_delta2_rs,
            ASSET_CLASS_PROFILES[asset_class].weight_volume_delta,
        )
        for symbol, asset_class in ASSET_PROFILE_MAP.items()
    }
