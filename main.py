"""
AlphaForge-Lite 命令行一键入口

串联"数据库初始化 -> 数据接入 -> 回测执行 -> 复盘报表"完整流程：

    - 不带 --data：只做数据库连接与初始化，确认成功后优雅退出；
    - 带 --data：在此基础上依次驱动 DataLoader -> BacktestConfig ->
      BacktestRunner.run()，拿到 run_id 后立刻实例化 BacktestReporter，
      自动打印生命周期分布、非共识归因、Lead Time 审计三张复盘看板。

数据接入、回测执行、报表渲染三层各自独立捕获异常，失败时打印带层级
标签的清晰错误信息并以非零状态码退出，不允许静默崩溃、吞掉异常。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from backtest.data_loader import DataLoader
from backtest.report import BacktestReporter
from backtest.runner import BacktestConfig, BacktestRunner
from config.settings import DB_PATH, RAW_DATA_DIR
from database.models import BacktestRun
from database.session import init_db


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AlphaForge-Lite 一键入口：数据库初始化 -> 数据接入 -> 回测 -> 复盘报表"
    )
    parser.add_argument(
        "--data",
        default=None,
        help="data/raw/ 目录下的历史大宽表文件名（.csv/.parquet），也可传绝对/相对路径。"
        "不指定该参数时仅初始化数据库后退出。",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="清空并重新初始化 SQLite 数据库——会删除旧的 .db/-wal/-shm/-journal 文件，"
        "不可恢复，请谨慎使用。",
    )
    parser.add_argument(
        "--db-path",
        default="alphaforge_lite.db",
        help="SQLite 数据库文件名或路径，默认 alphaforge_lite.db；"
        "只给文件名时落在项目既有的 database/ 目录下。",
    )
    return parser.parse_args()


def _resolve_db_path(db_path_arg: str) -> Path:
    """
    只给了裸文件名（如 "foo.db"）时，落在既有 database/ 目录下，
    与项目现有约定保持一致；给了路径（含目录部分）则原样使用。

    【全局扫描修复】原来只判断 candidate.parent == Path(".")——但
    Path("..").parent 和 Path(".").parent 也都等于 Path(".")（已用脚本
    验证），会把 --db-path ".." 误判成"裸文件名"，拼出
    DB_PATH.parent / ".." 逃出预期的 database 目录。--init-db 会基于
    这个路径尝试删除 .db/-wal/-shm/-journal 几个文件，虽然实际拼出的
    文件名（如 "..-wal"）在真实场景下几乎不会存在、不会真的删掉东西，
    但这个分类逻辑本身是错的，不应该依赖"恰好不会撞上真实文件"这种
    侥幸。显式排除 "." / ".." 这两个特殊路径分量，不当作裸文件名处理。
    """
    candidate = Path(db_path_arg)
    is_bare_filename = candidate.parent == Path(".") and candidate.name not in ("", ".", "..")
    if is_bare_filename:
        return DB_PATH.parent / candidate.name
    return candidate


def _resolve_data_path(data_arg: str) -> Path:
    candidate = Path(data_arg)
    return candidate if candidate.is_absolute() or candidate.exists() else RAW_DATA_DIR / data_arg


# ----------------------------------------------------------------------
# 第一层：数据库初始化
# ----------------------------------------------------------------------


def _init_database(db_path: Path, reset: bool) -> None:
    try:
        if reset:
            removed = []
            for suffix in ("", "-wal", "-shm", "-journal"):
                stale = Path(str(db_path) + suffix)
                if stale.exists():
                    stale.unlink()
                    removed.append(stale.name)
            if removed:
                print(f"[数据库层] 已清空旧数据库文件: {', '.join(removed)}")
        init_db(db_path)
        print(f"[数据库层] 数据库已就绪: {db_path}")
    except OSError as exc:
        print(f"[数据库层] 初始化失败（文件系统错误）: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # peewee 连接/建表异常等
        print(f"[数据库层] 初始化失败: {exc}", file=sys.stderr)
        sys.exit(1)


# ----------------------------------------------------------------------
# 第二层：数据接入
# ----------------------------------------------------------------------


def _load_data(data_arg: str) -> pd.DataFrame:
    resolved = _resolve_data_path(data_arg)
    if not resolved.exists():
        print(f"[数据接入层] 文件不存在: {resolved}", file=sys.stderr)
        sys.exit(1)

    print(f"[数据接入层] 正在加载大宽表: {resolved}")
    try:
        data = DataLoader().load_path(resolved)
    except ValueError as exc:
        print(f"[数据接入层] Schema 校验失败: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[数据接入层] 加载失败: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"[数据接入层] 加载完成：{len(data)} 行，涉及 {data['symbol'].nunique()} 个资产")
    return data


# ----------------------------------------------------------------------
# 第三层：回测执行
# ----------------------------------------------------------------------


def _run_backtest(data: pd.DataFrame, data_arg: str) -> BacktestRun:
    config = BacktestConfig(data_source=data_arg)  # 探测器/状态机均使用出厂默认参数
    print("[回测执行层] 开始跑批：CCS 全量向量化计算 -> 状态机按时间步推进")
    try:
        run = BacktestRunner(data=data, config=config).run()
    except Exception as exc:
        print(f"[回测执行层] 执行失败: {exc}", file=sys.stderr)
        sys.exit(1)

    if run.status != "SUCCESS":
        print(f"[回测执行层] 运行结束但状态异常: status={run.status}, notes={run.notes}", file=sys.stderr)
        sys.exit(1)

    print(f"[回测执行层] 完成，run_id={run.run_id}（{run.notes}）")
    return run


# ----------------------------------------------------------------------
# 第四层：复盘报表（最高审判台）
# ----------------------------------------------------------------------


def _print_report(run_id: str) -> None:
    print("[报表渲染层] 正在生成复盘报告……")
    try:
        reporter = BacktestReporter(run_id=run_id)
        reporter.print_report()
    except Exception as exc:
        print(f"[报表渲染层] 渲染失败: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    args = _parse_args()
    db_path = _resolve_db_path(args.db_path)
    _init_database(db_path, reset=args.init_db)

    if not args.data:
        print("[AlphaForge-Lite] 未指定 --data，仅完成数据库初始化，安全退出。")
        return

    data = _load_data(args.data)
    run = _run_backtest(data, args.data)
    _print_report(run.run_id)


if __name__ == "__main__":
    main()
