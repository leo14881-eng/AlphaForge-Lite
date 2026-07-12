"""
真实历史行情下载工具

用 ccxt 从 Binance 公开行情接口（现货 K 线，无需 API Key）批量抓取
一批主流加密资产近 3 年的日线数据，规整成符合 DataLoader /
detectors.cs_score.REQUIRED_COLUMNS 要求的长表，保存为
data/raw/crypto_market_daily.csv。

这是一个独立的一次性数据采集脚本，不属于回测核心链路的一部分——
DataLoader / BacktestRunner 等模块不依赖 ccxt，只依赖它产出的 CSV 文件。

运行前需要安装：
    pip install ccxt

用法：
    python data/download_data.py
    python data/download_data.py --symbols BTC/USDT,ETH/USDT --start 2024-01-01 --end 2026-01-01
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 本脚本位于 data/ 子目录，直接 `python data/download_data.py` 运行时
# 项目根目录不会自动出现在 sys.path 里，这里显式补上，才能复用
# config/detectors 里已经定义好的常量，避免自己再抄一份 schema 定义。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

try:
    import ccxt
except ImportError as exc:  # pragma: no cover - 环境提示，非业务逻辑
    raise SystemExit(
        "缺少 ccxt 依赖，请先运行: pip install ccxt"
    ) from exc

from config.settings import RAW_DATA_DIR
from detectors.cs_score import REQUIRED_COLUMNS

DEFAULT_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "LINK/USDT",
    "ADA/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "DOT/USDT",
    "LTC/USDT",
    "TRX/USDT",
)
DEFAULT_START = "2023-01-01"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_OUTPUT = "crypto_market_daily.csv"
_KLINES_PER_REQUEST = 1000  # Binance 现货 K 线单次请求上限（保守取值）
_TURNOVER_RATE_RANGE = (0.005, 0.05)  # 换手率代理指标映射到的目标区间


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 Binance 公开接口批量下载加密资产日线历史数据")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help=f"逗号分隔的交易对列表，默认: {','.join(DEFAULT_SYMBOLS)}",
    )
    parser.add_argument("--start", default=DEFAULT_START, help=f"起始日期 YYYY-MM-DD，默认 {DEFAULT_START}")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD，默认取当前日期")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help="K 线周期，默认 1d（日线）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"输出文件名，默认 {DEFAULT_OUTPUT}（落在 data/raw/ 下）")
    return parser.parse_args()


def _fetch_symbol_ohlcv(exchange: "ccxt.Exchange", symbol: str, timeframe: str, since_ms: int, until_ms: int) -> list[list]:
    """分页拉取单个交易对在 [since_ms, until_ms] 区间内的全部 K 线"""
    rows: list[list] = []
    cursor = since_ms
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=_KLINES_PER_REQUEST)
        if not batch:
            break
        rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts <= cursor:  # 防止交易所返回异常数据导致死循环
            break
        cursor = last_ts + 1
        if len(batch) < _KLINES_PER_REQUEST:
            break  # 已经拿到该交易对的最新数据，无需再翻页
    return [row for row in rows if row[0] <= until_ms]


def _estimate_turnover_rate(volume: pd.Series) -> pd.Series:
    """
    基于成交量对数的换手率代理指标。

    真实换手率 = 成交量 / 流通量，但可靠的分币种流通量数据不易稳定获取；
    这里采用对 log1p(volume) 做 min-max 归一化、映射到贴近真实换手率量级
    的 [0.5%, 5%] 区间的替代方案——保留了原始成交量的相对强弱关系，
    足以支撑 CCSDetector.crowding_penalty 组件对"极端值"的滚动 z-score
    判定，但不等价于真实的资产换手率，仅作本地沙盒验证使用。
    """
    log_vol = np.log1p(volume)
    v_min, v_max = log_vol.min(), log_vol.max()
    low, high = _TURNOVER_RATE_RANGE
    if v_max - v_min < 1e-9:
        return pd.Series(sum(_TURNOVER_RATE_RANGE) / 2, index=volume.index)
    normalized = (log_vol - v_min) / (v_max - v_min)
    return low + normalized * (high - low)


def main() -> None:
    args = _parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if len(symbols) < 10:
        print(f"[提示] 当前只配置了 {len(symbols)} 个交易对，建议 >= 10 个以获得更有意义的截面对比", file=sys.stderr)

    exchange = ccxt.binance({"enableRateLimit": True})
    since_ms = exchange.parse8601(f"{args.start}T00:00:00Z")
    until_ms = exchange.parse8601(f"{args.end}T00:00:00Z") if args.end else int(time.time() * 1000)

    frames: list[pd.DataFrame] = []
    for i, symbol in enumerate(symbols, start=1):
        print(f"[下载层] ({i}/{len(symbols)}) 正在拉取 {symbol} {args.timeframe} K线 ...")
        try:
            rows = _fetch_symbol_ohlcv(exchange, symbol, args.timeframe, since_ms, until_ms)
        except Exception as exc:  # 网络抖动、交易对不存在等，跳过不中断整批下载
            print(f"[下载层] {symbol} 拉取失败，跳过: {exc}", file=sys.stderr)
            continue

        if not rows:
            print(f"[下载层] {symbol} 没有返回任何数据，跳过", file=sys.stderr)
            continue

        df = pd.DataFrame(rows, columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True).dt.tz_localize(None)
        df["symbol"] = symbol.replace("/", "")  # BTC/USDT -> BTCUSDT，与项目内 symbol 命名约定一致
        df["turnover_rate"] = _estimate_turnover_rate(df["volume"])
        frames.append(df[["timestamp", "symbol", "open", "high", "low", "close", "volume", "turnover_rate"]])
        print(f"[下载层] {symbol} 完成，共 {len(df)} 根K线")

    if not frames:
        print("[下载层] 没有任何交易对下载成功，未生成输出文件", file=sys.stderr)
        sys.exit(1)

    wide_table = pd.concat(frames, ignore_index=True).sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    missing = set(REQUIRED_COLUMNS) - set(wide_table.columns)
    if missing:  # 理论上不会发生，留作契约断言，出现即说明本脚本逻辑有 bug
        raise RuntimeError(f"产出的数据缺少 DataLoader 必需列: {sorted(missing)}，请检查脚本逻辑")

    output_path = RAW_DATA_DIR / args.output
    wide_table.to_csv(output_path, index=False)
    print(
        f"\n[完成] 已保存 {len(wide_table)} 行、{wide_table['symbol'].nunique()} 个资产的数据到: {output_path}"
    )
    print("可直接用 --data 参数跑回测，例如：")
    print(f"    python main.py --data {args.output}")


if __name__ == "__main__":
    main()
