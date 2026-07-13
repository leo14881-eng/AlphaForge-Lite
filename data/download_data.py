"""
真实历史行情下载工具

用 ccxt 从 Binance 公开行情接口（现货 K 线，无需 API Key）批量抓取
主流资产 + "史诗级妖币/神币"资产池的日线数据，规整成符合 DataLoader /
detectors.cs_score.REQUIRED_COLUMNS 要求的长表，保存为
data/raw/crypto_market_daily.csv。

这是一个独立的一次性数据采集脚本，不属于回测核心链路的一部分——
DataLoader / BacktestRunner 等模块不依赖 ccxt，只依赖它产出的 CSV 文件。

关于时间视界的诚实说明：本脚本会尝试从 2017-01-01 开始拉取，但
Binance 现货实际能返回的数据受限于每个资产的真实上市日期——EPIC_POOL
里没有任何一个资产早于 2020 年就已上市（多数 2023-2024 年才上市，
"币安人生"更是 2026-01 才上市），ccxt 只会返回上市之后的数据，这属于
预期行为（"跳过未上线时段"），不是 bug，脚本运行结束会打印每个资产的
实际数据起止时间，如实反映覆盖缺口。

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

from config.asset_profiles import MAINSTREAM_SYMBOLS_CCXT as MAINSTREAM_SYMBOLS
from config.settings import RAW_DATA_DIR
from detectors.cs_score import REQUIRED_COLUMNS

# 主流稳健资产池（v0.6 起沿用，run_tuning.py / run_regression_check.py
# 的历史校准结果均基于这个池子，新增 EPIC_POOL 不会改变这份列表本身）。
#
# 【全局扫描修复】不再本地硬编码一份 ccxt 斜杠格式的列表——改从
# config.asset_profiles.MAINSTREAM_SYMBOLS_CCXT 导入（由该模块唯一权威
# 的 MAINSTREAM_SYMBOLS 自动转换出 "BTC/USDT" 格式），避免这份清单在
# 四个文件里各自维护导致以后漂移不一致。

# 第六阶段新增：15 个"史诗级妖币/神币"资产池，覆盖高爆发、剧烈洗盘乃至
# 归零的极端行情（v0.9 引入）。"币安人生"是 Binance 上真实存在的现货
# 交易对（base symbol 本身就是中文），已用 ccxt 实测确认存在且可正常
# 拉取；GOAT / MOODENG 在 Binance 现货没有挂牌，只有 USDT 本位永续合约
# （`GOAT/USDT:USDT`），脚本会自动做现货优先、期货兜底的符号解析。
EPIC_POOL_SYMBOLS: tuple[str, ...] = (
    "GALA/USDT",
    "AXS/USDT",
    "WIF/USDT",
    "FLOKI/USDT",
    "LUNA/USDT",
    "BONK/USDT",
    "PEPE/USDT",
    "SOL/USDT",  # 与主流池重复，合并时自动去重
    "TIA/USDT",
    "SUI/USDT",
    "币安人生/USDT",
    "ACT/USDT",
    "GOAT/USDT",  # 现货不存在，自动回退到 GOAT/USDT:USDT 永续合约
    "PNUT/USDT",
    "MOODENG/USDT",  # 现货不存在，自动回退到 MOODENG/USDT:USDT 永续合约
)

# 去重后的默认下载全集：主流池 + 妖币池
DEFAULT_SYMBOLS: tuple[str, ...] = tuple(
    dict.fromkeys([*MAINSTREAM_SYMBOLS, *EPIC_POOL_SYMBOLS])
)

DEFAULT_START = "2017-01-01"
DEFAULT_TIMEFRAME = "1d"
DEFAULT_OUTPUT = "crypto_market_daily.csv"
_KLINES_PER_REQUEST = 1000  # Binance 现货 K 线单次请求上限（保守取值）
_TURNOVER_RATE_RANGE = (0.005, 0.05)  # 换手率代理指标映射到的目标区间
_MAX_PAGE_RETRIES = 3  # 单页分页请求失败时的最大重试次数
_RETRY_BACKOFF_SECONDS = 2.0  # 重试退避基数（第 N 次重试等待 N 倍这个值）


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 Binance 公开接口批量下载加密资产日线历史数据")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="逗号分隔的交易对列表，默认 = 主流资产池 + 史诗妖币资产池（去重）",
    )
    parser.add_argument("--start", default=DEFAULT_START, help=f"起始日期 YYYY-MM-DD，默认 {DEFAULT_START}（实际能拿到多早的数据取决于各资产真实上市时间）")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD，默认取当前日期")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME, help="K 线周期，默认 1d（日线）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"输出文件名，默认 {DEFAULT_OUTPUT}（落在 data/raw/ 下）")
    return parser.parse_args()


def _resolve_tradable_symbol(exchange: "ccxt.Exchange", spot_symbol: str) -> str | None:
    """
    优先现货，现货不存在则尝试同名 USDT 本位永续合约（如 GOAT、MOODENG
    在 Binance 只有期货挂牌，没有现货）。两者都不存在则返回 None，
    由调用方跳过该资产（异常隔离，不中断整批下载）。
    """
    if spot_symbol in exchange.markets:
        return spot_symbol
    futures_symbol = f"{spot_symbol}:USDT"
    if futures_symbol in exchange.markets:
        return futures_symbol
    return None


def _normalize_output_symbol(market_symbol: str) -> str:
    """'GOAT/USDT:USDT' -> 'GOATUSDT'，'币安人生/USDT' -> '币安人生USDT'"""
    base_quote = market_symbol.split(":")[0]
    return base_quote.replace("/", "")


def _fetch_symbol_ohlcv(exchange: "ccxt.Exchange", symbol: str, timeframe: str, since_ms: int, until_ms: int) -> list[list]:
    """
    分页拉取单个交易对在 [since_ms, until_ms] 区间内的全部 K 线。

    【全局扫描修复：网络异常不再丢弃已下载的部分数据】此前任何一页
    请求失败都会让整个函数直接抛异常，调用方 main() 的 try/except 会把
    这个 symbol 已经拉到的若干页数据（局部变量 rows）全部丢弃、跳过
    整个 symbol，没有任何重试。9 年日线数据每个 symbol 通常需要 3-4 次
    分页请求，26 个 symbol 顺序执行，网络抖动概率不低，一次抖动就
    白费前面几页已经成功的请求。现在改成：单页请求失败先重试
    _MAX_PAGE_RETRIES 次（线性退避），仍然失败就带着已经成功拿到的
    部分数据提前返回（不抛异常），调用方打印警告说明数据不完整。脚本
    本身是幂等的全量重新拉取（不是增量更新），重跑一次就能自然补全，
    "部分数据"不会导致脏数据，只是这次运行的时间覆盖范围不完整，
    比"整个 symbol 一条数据都没有"要好得多。
    """
    rows: list[list] = []
    cursor = since_ms
    while cursor < until_ms:
        batch: list[list] | None = None
        for attempt in range(1, _MAX_PAGE_RETRIES + 1):
            try:
                batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=_KLINES_PER_REQUEST)
                break
            except Exception as exc:
                if attempt == _MAX_PAGE_RETRIES:
                    print(
                        f"[下载层] {symbol} 分页请求连续 {_MAX_PAGE_RETRIES} 次失败"
                        f"（游标={cursor}）：{exc}；保留已拉到的 {len(rows)} 条K线，"
                        "提前结束该资产的拉取（重跑脚本可补全缺口）",
                        file=sys.stderr,
                    )
                    return [row for row in rows if row[0] <= until_ms]
                wait_seconds = _RETRY_BACKOFF_SECONDS * attempt
                print(
                    f"[下载层] {symbol} 第 {attempt}/{_MAX_PAGE_RETRIES} 次分页请求失败："
                    f"{exc}，{wait_seconds:.0f} 秒后重试",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
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
    exchange.load_markets()  # 显式预加载，_resolve_tradable_symbol 需要用到 markets 做存在性判断
    since_ms = exchange.parse8601(f"{args.start}T00:00:00Z")
    until_ms = exchange.parse8601(f"{args.end}T00:00:00Z") if args.end else int(time.time() * 1000)

    frames: list[pd.DataFrame] = []
    coverage_report: list[tuple[str, str, str, str]] = []  # (原始symbol, 实际解析symbol, 起始日期, 结束日期)

    for i, symbol in enumerate(symbols, start=1):
        resolved = _resolve_tradable_symbol(exchange, symbol)
        if resolved is None:
            print(f"[下载层] ({i}/{len(symbols)}) {symbol} 在 Binance 现货/期货均未找到挂牌，跳过", file=sys.stderr)
            continue

        market_kind = "期货永续" if ":" in resolved else "现货"
        print(f"[下载层] ({i}/{len(symbols)}) 正在拉取 {symbol}（{market_kind}: {resolved}）{args.timeframe} K线 ...")
        try:
            rows = _fetch_symbol_ohlcv(exchange, resolved, args.timeframe, since_ms, until_ms)
        except Exception as exc:  # 网络抖动、交易对不存在等，跳过不中断整批下载
            print(f"[下载层] {symbol} 拉取失败，跳过: {exc}", file=sys.stderr)
            continue

        if not rows:
            print(f"[下载层] {symbol} 没有返回任何数据，跳过", file=sys.stderr)
            continue

        df = pd.DataFrame(rows, columns=["timestamp_ms", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp_ms"], unit="ms", utc=True).dt.tz_localize(None)
        df["symbol"] = _normalize_output_symbol(resolved)
        df["turnover_rate"] = _estimate_turnover_rate(df["volume"])
        frames.append(df[["timestamp", "symbol", "open", "high", "low", "close", "volume", "turnover_rate"]])

        coverage_report.append(
            (symbol, resolved, str(df["timestamp"].min().date()), str(df["timestamp"].max().date()))
        )
        print(f"[下载层] {symbol} 完成，共 {len(df)} 根K线（{df['timestamp'].min().date()} ~ {df['timestamp'].max().date()}）")

    if not frames:
        print("[下载层] 没有任何交易对下载成功，未生成输出文件", file=sys.stderr)
        sys.exit(1)

    wide_table = pd.concat(frames, ignore_index=True).sort_values(["symbol", "timestamp"]).reset_index(drop=True)

    missing = set(REQUIRED_COLUMNS) - set(wide_table.columns)
    if missing:  # 理论上不会发生，留作契约断言，出现即说明本脚本逻辑有 bug
        raise RuntimeError(f"产出的数据缺少 DataLoader 必需列: {sorted(missing)}，请检查脚本逻辑")

    output_path = RAW_DATA_DIR / args.output
    # 显式 utf-8-sig：资产池里含"币安人生"等原生中文 symbol，必须显式声明
    # 编码，避免 Windows 平台默认代码页猜错导致中文写入损坏。
    wide_table.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(
        f"\n[完成] 已保存 {len(wide_table)} 行、{wide_table['symbol'].nunique()} 个资产的数据到: {output_path}"
    )

    print("\n[数据覆盖报告] 各资产实际数据起止时间（如实反映早期历史缺口，不做任何美化）：")
    for original, resolved, start_date, end_date in coverage_report:
        flag = " <- 早于2020年无数据" if start_date >= "2020-01-01" else ""
        print(f"  {original:16s} ({resolved:20s}): {start_date} ~ {end_date}{flag}")

    print("\n可直接用 --data 参数跑回测，例如：")
    print(f"    python main.py --data {args.output}")


if __name__ == "__main__":
    main()
