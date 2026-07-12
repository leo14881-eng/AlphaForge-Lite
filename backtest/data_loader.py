"""
历史大宽表数据加载模块

DataLoader 负责从 data/raw/ 目录（或任意指定路径）加载 CSV / Parquet
格式的历史时序大宽表，做最基础的 schema 校验与规范化，返回长表格式
（timestamp / symbol / close / volume / turnover_rate [/ funding_rate]）
的多资产时序 DataFrame，供 detectors.cs_score.CCSDetector 与
backtest.runner.BacktestRunner 直接消费。

必需列复用 detectors.cs_score 中的定义，避免"加载层校验的字段"与
"探测器实际需要的字段"两处定义漂移。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from config.settings import RAW_DATA_DIR
from detectors.cs_score import OPTIONAL_FUNDING_RATE_COLUMN, REQUIRED_COLUMNS

_NUMERIC_COLUMNS: tuple[str, ...] = ("close", "volume", "turnover_rate")
_SUPPORTED_SUFFIXES: dict[str, str] = {".csv": "csv", ".parquet": "parquet", ".pq": "parquet"}


@dataclass
class DataLoader:
    """
    大宽表加载器。

    Attributes:
        raw_dir: 相对文件名（不含目录）默认从该目录下解析，默认为
            config.settings.RAW_DATA_DIR。
    """

    raw_dir: Path = field(default_factory=lambda: RAW_DATA_DIR)

    def load(self, filename: str) -> pd.DataFrame:
        """从 raw_dir 下加载指定文件名（如 'wide_table.csv'）"""
        return self._load_and_validate(Path(self.raw_dir) / filename)

    def load_path(self, path: str | Path) -> pd.DataFrame:
        """加载任意绝对/相对路径指定的文件，不受 raw_dir 限制"""
        return self._load_and_validate(Path(path))

    def _load_and_validate(self, path: Path) -> pd.DataFrame:
        if not path.exists():
            raise FileNotFoundError(f"历史大宽表文件不存在: {path}")

        file_format = _SUPPORTED_SUFFIXES.get(path.suffix.lower())
        if file_format is None:
            raise ValueError(
                f"不支持的文件格式 '{path.suffix}'（{path.name}），仅支持 .csv / .parquet"
            )

        # 显式指定 utf-8-sig：资产池里存在"币安人生"这类原生中文 symbol，
        # 不显式声明编码在 Windows 平台上容易因默认代码页（如 GBK/CP1252）
        # 猜错而导致中文 symbol 被静默损坏或匹配失败，必须显式对齐。
        df = pd.read_csv(path, encoding="utf-8-sig") if file_format == "csv" else pd.read_parquet(path)

        missing = set(REQUIRED_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"{path.name} 缺少必需列: {sorted(missing)}")

        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        for col in _NUMERIC_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="raise")
        if OPTIONAL_FUNDING_RATE_COLUMN in df.columns:
            df[OPTIONAL_FUNDING_RATE_COLUMN] = pd.to_numeric(
                df[OPTIONAL_FUNDING_RATE_COLUMN], errors="raise"
            )

        # 按 (symbol, timestamp) 排序去重：同一资产同一时间戳出现多行时
        # 保留最后一条（约定为最新/最终修正值），保证下游滚动窗口计算
        # 不会因重复时间戳产生错位。
        df = (
            df.sort_values(["symbol", "timestamp"])
            .drop_duplicates(subset=["symbol", "timestamp"], keep="last")
            .reset_index(drop=True)
        )
        return df
