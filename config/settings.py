"""
全局配置模块

集中管理项目路径、数据库连接方式等基础配置项。
本项目定位为本地沙盒验证工具，不引入远程配置中心，
所有配置均通过本文件的常量或环境变量覆盖完成。
"""
from __future__ import annotations

import os
from pathlib import Path

# 项目根目录（本文件所在目录的上一级）
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# 原始行情大宽表（CSV / Parquet）存放目录
RAW_DATA_DIR: Path = BASE_DIR / "data" / "raw"

# 经过清洗 / 特征加工后的中间数据存放目录
PROCESSED_DATA_DIR: Path = BASE_DIR / "data" / "processed"

# 运行日志目录
LOG_DIR: Path = BASE_DIR / "logs"

# SQLite 数据库文件路径，可通过环境变量 ALPHAFORGE_DB_PATH 覆盖，
# 便于在测试环境中切换到内存库或临时文件库。
DB_PATH: Path = Path(
    os.environ.get("ALPHAFORGE_DB_PATH", str(BASE_DIR / "database" / "alphaforge.db"))
)

# 首次运行时确保关键目录存在
for _dir in (RAW_DATA_DIR, PROCESSED_DATA_DIR, LOG_DIR, DB_PATH.parent):
    _dir.mkdir(parents=True, exist_ok=True)
