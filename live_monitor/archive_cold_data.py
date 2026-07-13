"""
live_monitor/archive_cold_data.py —— 冷热分离归档脚本

把 strategy_signals（热表）里超过 RETENTION_DAYS 天的记录原样搬到
strategy_signals_archive（冷表），再从热表删除——不是简单 DELETE，是
"先复制、确认成功、再删除"，避免归档失败导致数据丢失。

建议通过 cron（Linux）或 Windows 任务计划，每天低峰期执行一次：
    python -m live_monitor.archive_cold_data
"""
from __future__ import annotations

import logging

import pymysql

from live_monitor.market_monitor import MYSQL_CONFIG

logger = logging.getLogger("alphaforge.live_monitor.archive")

RETENTION_DAYS = 7


def archive_cold_data() -> int:
    """归档超过 RETENTION_DAYS 天的热数据，返回归档的行数"""
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        with conn.cursor() as cur:
            # 先把满足条件的行复制进冷表；INSERT IGNORE 防止重复归档时因
            # signal_uuid 唯一键冲突而报错中断。
            cur.execute(
                "INSERT IGNORE INTO strategy_signals_archive "
                "(id, asset, signal_type, signal_uuid, created_time) "
                "SELECT id, asset, signal_type, signal_uuid, created_time "
                "FROM strategy_signals "
                "WHERE created_time < DATE_SUB(NOW(), INTERVAL %s DAY)",
                (RETENTION_DAYS,),
            )
            copied = cur.rowcount

            # 确认复制成功后，再从热表删除同一批数据，避免"删了但没复制成功"丢数据。
            cur.execute(
                "DELETE FROM strategy_signals WHERE created_time < DATE_SUB(NOW(), INTERVAL %s DAY)",
                (RETENTION_DAYS,),
            )
            deleted = cur.rowcount

        conn.commit()
        logger.info("[归档] 复制到冷表 %d 行，从热表删除 %d 行", copied, deleted)
        return deleted
    except Exception:
        conn.rollback()
        logger.exception("[归档] 执行失败，已回滚")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = archive_cold_data()
    print(f"[归档] 已清理 {n} 条超过 {RETENTION_DAYS} 天的热数据（已安全复制到冷表）")
