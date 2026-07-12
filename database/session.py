"""
数据库连接与初始化模块

peewee 的 SqliteDatabase 在 database.models 中以延迟绑定方式创建
（`pw.SqliteDatabase(None)`），本模块负责在应用启动时把它指向
config.settings.DB_PATH 指定的实际文件，并完成建表。

之所以延迟绑定，是为了让测试代码可以在不改动 database.models 的
前提下，将同一批 Model 类临时绑定到内存库（参见 tests/test_models.py）。
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config.settings import DB_PATH
from database.models import MODELS, db


def init_db(db_path: str | Path | None = None) -> None:
    """
    初始化数据库连接并建表，幂等操作，可在应用启动时安全重复调用。

    db_path: 显式指定数据库文件路径时使用该路径；不传则使用
        config.settings.DB_PATH。仅在数据库句柄第一次绑定时生效
        （即 db.database is None or db.deferred 时），进程内后续
        调用不会重新指向别的文件——这个限制也正是测试代码能用
        bind_ctx 临时切到内存库而不受影响的原因。

    pragmas 说明：
        - foreign_keys=1：启用外键约束，保证 StateTransitionLog 不会
          指向不存在的 Asset / BacktestRun；
        - journal_mode=wal：写前日志模式，减少回测批量写入时的锁竞争。
    """
    if db.database is None or db.deferred:
        target = Path(db_path) if db_path is not None else DB_PATH
        db.init(str(target), pragmas={"foreign_keys": 1, "journal_mode": "wal"})
    if db.is_closed():
        db.connect()
    db.create_tables(MODELS, safe=True)


@contextmanager
def get_db() -> Iterator[None]:
    """
    提供原子事务的上下文管理器，用法：

        with get_db():
            StateTransitionLog.create(...)
    """
    with db.atomic():
        yield
