-- live_monitor 子系统的 MySQL 留痕表
--
-- 冷热分离说明：strategy_signals 是热表，只保留最近 7 天数据，由
-- archive_cold_data.py 定时把超过 7 天的记录搬到 strategy_signals_archive
-- （同结构冷表）再从热表删除——保证大屏"今日"相关查询始终在小表上跑，
-- 同时历史全量流水（/api/v1/signals/history）通过 UNION 热表+冷表提供，
-- 不丢数据。
--
-- 【重要边界，勿混淆】这两张表（热表+冷表）只是纯审计/大屏历史留痕，
-- 不是 Java 实盘下单的读取源。Java 下单必须走 Redis Stream
-- （stream:strategy:signals）的消费组（Consumer Group）可靠消费+ACK，
-- 这是本子系统与 Java 执行端已确认维持的原始规格。写 MySQL 和写 Redis
-- Stream 是 market_monitor.py::SignalSink.persist_and_broadcast 里两条
-- 相互独立的旁路，MySQL 写失败不影响 Redis 广播，反之亦然——MySQL 不是
-- Redis Stream 的前置或中间环节，Java 不应该、也不需要读这两张表来决定
-- 下不下单。

CREATE TABLE IF NOT EXISTS `strategy_signals` (
    `id`                         BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT COMMENT '信号全局唯一主键',
    `asset`                      VARCHAR(32)      NOT NULL COMMENT '资产标的代码（如SOL、DOGE）',
    `signal_type`                VARCHAR(16)      NOT NULL COMMENT '信号状态：DISCOVERY-生成领导者 / EXIT-退出领导者',
    `signal_uuid`                VARCHAR(64)      NOT NULL COMMENT '策略逻辑生成的唯一波段防重键(UUID/波段ID)',
    `created_time`               DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '落库时间',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_signal_uuid` (`signal_uuid`),
    KEY `idx_created_time` (`created_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公共策略信号纯净留痕表（热表，近7天）';

CREATE TABLE IF NOT EXISTS `strategy_signals_archive` (
    `id`                         BIGINT UNSIGNED  NOT NULL COMMENT '沿用热表主键，归档时原样搬迁',
    `asset`                      VARCHAR(32)      NOT NULL COMMENT '资产标的代码（如SOL、DOGE）',
    `signal_type`                VARCHAR(16)      NOT NULL COMMENT '信号状态：DISCOVERY-生成领导者 / EXIT-退出领导者',
    `signal_uuid`                VARCHAR(64)      NOT NULL COMMENT '策略逻辑生成的唯一波段防重键(UUID/波段ID)',
    `created_time`               DATETIME         NOT NULL COMMENT '落库时间（原样保留，不用归档时间覆盖）',
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_signal_uuid` (`signal_uuid`),
    KEY `idx_created_time` (`created_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='公共策略信号纯净留痕表（冷表，超过7天的历史归档）';
