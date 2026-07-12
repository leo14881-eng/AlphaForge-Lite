"""
AlphaForge-Lite HTTP API 服务层

把 backtest.runner.BacktestRunner / backtest.report.BacktestReporter
包装成常驻的 HTTP 接口，进程启动后监听端口、持续接收请求，供习惯
"服务常驻、调接口"工作方式的调用方使用（用 `python run_api.py` 或
`uvicorn api.app:app` 启动，不会像 main.py 那样跑完就退出）。

v1 定位说明：本服务是同步阻塞式的薄封装——POST /runs 会在请求内
同步跑完整个回测。本项目面向本地静态大宽表的小规模沙盒场景，单次
回测通常在秒级到分钟级，暂不引入任务队列；数据规模变大后如需
异步化，可在此基础上接入 Celery/RQ，不影响其它模块。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backtest.data_loader import DataLoader
from backtest.report import BacktestReporter
from backtest.runner import BacktestConfig, BacktestRunner
from database.models import BacktestRun
from database.session import init_db


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(
    title="AlphaForge-Lite API",
    description="非共识资本聚集探测器 —— 回测执行与复盘审计的 HTTP 接口",
    version="0.6.0",
    lifespan=_lifespan,
)


class RunRequest(BaseModel):
    """POST /runs 的请求体：指定数据源与回测配置"""

    data_source: str  # 文件名（相对 data/raw/ 解析）或绝对/相对路径
    strategy_name: str = "non_consensus_accumulation"
    strategy_version: str = "v1"
    symbols: list[str] | None = None  # 不指定则使用数据中出现的全部资产


def _load_data(data_source: str):
    loader = DataLoader()
    path = Path(data_source)
    try:
        return loader.load_path(path) if path.is_absolute() or path.exists() else loader.load(data_source)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/runs")
def create_run(payload: RunRequest) -> dict:
    """同步跑一次完整回测，返回本次运行的 run_id 与最终状态"""
    data = _load_data(payload.data_source)
    config = BacktestConfig(
        strategy_name=payload.strategy_name,
        strategy_version=payload.strategy_version,
        data_source=payload.data_source,
        symbols=payload.symbols,
    )
    try:
        run = BacktestRunner(data=data, config=config).run()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"回测执行失败: {exc}") from exc

    return {"run_id": run.run_id, "status": run.status, "notes": run.notes}


@app.get("/runs")
def list_runs(limit: int = 20) -> list[dict]:
    """列出最近的回测运行（按创建时间倒序）"""
    query = BacktestRun.select().order_by(BacktestRun.created_at.desc()).limit(limit)
    return [
        {
            "run_id": r.run_id,
            "strategy_name": r.strategy_name,
            "strategy_version": r.strategy_version,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in query
    ]


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    """查询某次回测运行的元数据"""
    run = BacktestRun.get_or_none(BacktestRun.run_id == run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"未找到 run_id={run_id}")
    return {
        "run_id": run.run_id,
        "strategy_name": run.strategy_name,
        "strategy_version": run.strategy_version,
        "status": run.status,
        "data_start_ts": run.data_start_ts.isoformat(),
        "data_end_ts": run.data_end_ts.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "notes": run.notes,
    }


@app.get("/runs/{run_id}/report")
def get_report(run_id: str) -> dict:
    """获取某次回测运行的完整复盘报告（生命周期分布/非共识归因/Lead Time 审计）"""
    try:
        reporter = BacktestReporter(run_id=run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return reporter.to_dict()
