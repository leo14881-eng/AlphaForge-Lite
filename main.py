"""
项目入口脚本

当前版本仅负责初始化本地 SQLite 库结构，验证脚手架可正常跑通。
数据加载、探测器、回测流程将在后续迭代中接入本入口。
"""
from __future__ import annotations

from database.session import init_db
from config.settings import DB_PATH


def main() -> None:
    init_db()
    print(f"[AlphaForge-Lite] 数据库已就绪: {DB_PATH}")


if __name__ == "__main__":
    main()
