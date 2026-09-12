# -*- coding: utf-8 -*-
"""
app.py — "链接转文字" 工作台主服务

用法:
    python app.py                 # 监听 0.0.0.0:8300（手机同 WiFi 可访问）
    PORT=9000 python app.py       # 自定义端口

环境变量:
    WHISPER_MODEL   whisper 模型档位: tiny/base/small/medium/large-v3（默认 medium）
    PORT            监听端口（默认 8300）
    KEEP_HISTORY    是否保留历史记录文件（默认 1）
"""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

import extractor
from cookies_manager import get_cookie_file_for
import transcriber

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="链接转文字工作台")

# ---------------- 任务存储（内存 + 可选落盘） ----------------
JOBS = {}            # job_id -> dict
JOBS_LOCK = threading.Lock()
WORK_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs")
HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")
os.makedirs(WORK_ROOT, exist_ok=True)


def _save_history(job_id: str, result: dict):
    if os.environ.get("KEEP_HISTORY", "1") != "1":
        return
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        rec = {"job_id": job_id, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
               "url": result.get("url", ""), "title": result.get("title", ""),
               "uploader": result.get("uploader", ""),
               "duration": result.get("duration", 0),
               "full_text": result.get("full_text", ""),
               "segments": result.get("segments", [])}
        with open(os.path.join(HISTORY_DIR, f"{job_id}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning("保存历史失败: %s", e)


class ExtractRequest(BaseModel):
    text: str            # 用户粘贴的完整分享文本（或纯 URL）


def _run_job(job_id: str, raw_text: str):
    job = JOBS[job_id]
    workdir = os.path.join(WORK_ROOT, job_id)
    os.makedirs(workdir, exist_ok=True)
    try:
        # 1. 提取 URL
        url = extractor.extract_url(raw_text)
        if not url:
            raise ValueError("粘贴的内容里没有找到链接，请复制包含 https:// 的分享文本")
        job.update(stage="解析链接", progress=3, url=url)

        # 2. cookies（抖音/B站自动处理）
        cookie_file = get_cookie_file_for(url)

        # 3. 探测元数据
        info = extractor.probe_info(url, cookie_file)
        job.update(title=info["title"], uploader=info["uploader"],
                   duration=info["duration"])

        # 4. 下载 + 抽音轨
        def dl_progress(p, stage):
            job.update(progress=p, stage=stage)
        wav_path, meta = extractor.download_audio(url, workdir, cookie_file,
                                                  progress_cb=dl_progress)
        job.update(title=meta["title"] or info["title"],
                   uploader=meta["uploader"] or info["uploader"],
                   duration=meta["duration"] or info["duration"])

        # 5. 转写
        def tr_progress(p, stage):
            job.update(progress=p, stage=stage)
        result = transcriber.transcribe(wav_path, progress_cb=tr_progress)

        # 6. 完成
        job.update(
            stage="完成", progress=100, status="done",
            segments=result["segments"], full_text=result["full_text"],
        )
        _save_history(job_id, {**job, "url": url})
    except Exception as e:
        log.exception("任务 %s 失败", job_id)
        job.update(status="error", stage="失败", error=str(e)[:500])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@app.post("/api/extract")
def create_job(req: ExtractRequest):
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"id": job_id, "status": "running", "progress": 0,
                        "stage": "排队中", "created": time.time(),
                        "text": req.text[:2000]}
    threading.Thread(target=_run_job, args=(job_id, req.text),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        # 尝试从历史恢复（服务重启后仍可查看结果）
        p = os.path.join(HISTORY_DIR, f"{job_id}.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            rec.update(status="done", progress=100, stage="完成")
            return rec
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {k: v for k, v in job.items() if k != "text"}


@app.get("/api/history")
def history_list():
    items = []
    if os.path.isdir(HISTORY_DIR):
        for fn in sorted(os.listdir(HISTORY_DIR), reverse=True):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(HISTORY_DIR, fn), encoding="utf-8") as f:
                    r = json.load(f)
                items.append({"job_id": r["job_id"], "time": r["time"],
                              "title": r.get("title", "")[:60],
                              "duration": r.get("duration", 0)})
            except Exception:
                pass
    return {"items": items[:100]}


@app.delete("/api/history/{job_id}")
def history_delete(job_id: str):
    p = os.path.join(HISTORY_DIR, f"{job_id}.json")
    if os.path.exists(p):
        os.remove(p)
    return {"ok": True}


# 静态前端
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8300"))
    log.info("启动服务: http://0.0.0.0:%d  （手机连同一 WiFi 访问本机 IP:%d）", port, port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
