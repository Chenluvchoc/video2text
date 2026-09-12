# -*- coding: utf-8 -*-
"""
transcriber.py — faster-whisper 本地转写封装

- 模型从本地目录加载（首次运行自动从 ModelScope 国内源下载，无需科学上网）
- 模型常驻内存，多次任务不重复加载
- 简体引导 + 防重复循环参数
"""
import os
import logging

log = logging.getLogger("transcriber")

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# 模型下载源（国内可直连）
MODELSCOPE_URL = "https://modelscope.cn/models/Systran/faster-whisper-{size}/resolve/master/{file}"
MODEL_FILES = ["config.json", "tokenizer.json", "vocabulary.txt",
               "preprocessor_config.json", "model.bin"]

_model_cache = {}  # size -> WhisperModel


def _download_model(size: str):
    """从 ModelScope 下载模型到本地目录"""
    import urllib.request
    import shutil
    d = os.path.join(MODELS_DIR, f"faster-whisper-{size}")
    os.makedirs(d, exist_ok=True)
    bin_path = os.path.join(d, "model.bin")
    for f in MODEL_FILES:
        dst = os.path.join(d, f)
        if f == "model.bin" and os.path.exists(bin_path) and os.path.getsize(bin_path) > 10_000_000:
            continue  # 主文件已存在（断点续传逻辑简单化：存在即认为完整）
        if f != "model.bin" and os.path.exists(dst) and os.path.getsize(dst) > 0:
            continue
        url = MODELSCOPE_URL.format(size=size, file=f)
        log.info("下载模型文件 %s …", f)
        tmp = dst + ".part"
        try:
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as w:
                shutil.copyfileobj(r, w)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                try: os.remove(tmp)
                except OSError: pass
        # model.bin 可能 404（ModelScope 上部分小模型没有 preprocessor），忽略小文件失败
    return d


def get_model(size: str = None):
    """获取（并缓存）whisper 模型"""
    from faster_whisper import WhisperModel
    size = size or os.environ.get("WHISPER_MODEL", "medium")
    if size in _model_cache:
        return _model_cache[size]
    local_dir = os.path.join(MODELS_DIR, f"faster-whisper-{size}")
    bin_path = os.path.join(local_dir, "model.bin")
    if not (os.path.exists(bin_path) and os.path.getsize(bin_path) > 10_000_000):
        log.info("本地未找到 %s 模型，开始下载 …", size)
        local_dir = _download_model(size)
    log.info("加载 whisper-%s（int8 CPU）…", size)
    model = WhisperModel(local_dir, device="cpu", compute_type="int8",
                         cpu_threads=max(4, (os.cpu_count() or 8) - 1))
    _model_cache[size] = model
    return model


def transcribe(wav_path: str, size: str = None, progress_cb=None) -> dict:
    """
    转写 wav，返回 {segments: [{start, end, text}], full_text, lang}
    progress_cb(pct, stage) 用于上层进度展示
    """
    model = get_model(size)
    segments, info = model.transcribe(
        wav_path,
        language="zh",
        task="transcribe",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
        initial_prompt="以下是普通话简体中文口播内容，请用简体中文转写。",
        condition_on_previous_text=False,   # 防止错误扩散/重复循环
        compression_ratio_threshold=2.6,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
    )
    out = []
    texts = []
    total = info.duration or 1
    for s in segments:
        text = (s.text or "").strip()
        if not text:
            continue
        out.append({"start": round(s.start, 1), "end": round(s.end, 1),
                    "text": text})
        texts.append(text)
        if progress_cb:
            progress_cb(70 + int(min(s.end, total) / total * 29), "转写中")
    return {
        "segments": out,
        "full_text": "".join(texts),
        "lang": info.language,
    }
