#!/usr/bin/env python3
"""FastAPI app 入口。

啟動：
    .venv/bin/uvicorn main:app --host 0.0.0.0 --port 9090 --reload
    或
    .venv/bin/python main.py
"""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import config
from agent import vector_search
from api.routes import router


# ── Lifespan：啟動時建立 SOP 向量索引 ─────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("正在建立 SOP 向量索引（首次啟動需下載模型，請稍候）...", flush=True)
    try:
        count = vector_search.index_all_sops(config.SOP_DIR)
        print(f"已索引 {count} 個入口 case。", flush=True)
    except Exception as e:
        print(f"[警告] Vector Search 初始化失敗：{e}", flush=True)
        print("將以無 SOP 搜尋模式啟動（所有問題進入 fallback 模式）。", flush=True)
    yield


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="智能客服 Agent",
    description="半導體製程疑難雜症排查助手",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS（內網環境）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 路由
app.include_router(router)

# 靜態前端檔案（frontend/dist/ 目錄）
_frontend_dir = Path(__file__).parent / "frontend" / "dist"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=9090, reload=True)
