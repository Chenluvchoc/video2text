# -*- coding: utf-8 -*-
"""
extractor.py — 链接解析 / 媒体下载 / 音轨抽取

平台策略：
- B 站：API 直连（稳定，纯 HTTP 无需浏览器）
- 抖音/快手/小红书等：yt-dlp + 平台 cookies（抖音需新鲜 cookies）

核心流程：
    分享文本 → 正则提取 URL → 探测元数据 → 下载最低画质 → ffmpeg 抽 16kHz 音轨 → 删原件
"""
import os
import re
import glob
import subprocess
import logging

import requests

log = logging.getLogger("extractor")

URL_RE = re.compile(r'https?://[^\s，,。；;！!？?"\'（）()\[\]【】<>]+')

DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
BILI_HOSTS = ("bilibili.com", "b23.tv", "biliapi.net")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def extract_url(text: str) -> str:
    """从任意分享文本中提取第一个 URL"""
    if not text:
        return ""
    m = URL_RE.search(text)
    if not m:
        return ""
    u = m.group(0)
    return u if u.endswith("/") else u + "/"


def _is_bili(url: str) -> bool:
    return any(h in url for h in BILI_HOSTS)


# ================================================================ B 站专用（API 直连）
_BILI_BVID_RE = re.compile(r'(BV[0-9A-Za-z]{10})')


def _bili_api_headers(cookie: str) -> dict:
    return {"User-Agent": UA, "Referer": "https://www.bilibili.com",
            "Cookie": cookie}


def _bili_get_cookie() -> str:
    """获取 B 站最小可用 cookie（buvid3/buvid4），纯 HTTP 无需浏览器"""
    try:
        r = requests.get("https://api.bilibili.com/x/frontend/finger/spi",
                         headers={"User-Agent": UA}, timeout=10)
        d = r.json().get("data", {})
        return f"buvid3={d.get('b_3','')}; buvid4={d.get('b_4','')}"
    except Exception as e:
        log.warning("B 站 cookie 获取失败: %s", e)
        return ""


def _resolve_b23(url: str) -> str:
    """b23.tv 短链 → 真实 URL"""
    try:
        r = requests.get(url, headers={"User-Agent": UA},
                         allow_redirects=True, timeout=10)
        return r.url
    except Exception:
        return url


def _bili_view(bvid: str) -> dict:
    """调用 view 接口拿视频信息"""
    ck = _bili_get_cookie()
    r = requests.get("https://api.bilibili.com/x/web-interface/view",
                     params={"bvid": bvid}, headers=_bili_api_headers(ck),
                     timeout=15)
    data = r.json().get("data") or {}
    if not data:
        raise ValueError("B 站接口未返回视频信息（视频可能不存在、审核中或需要登录）")
    return data


def bili_probe(url: str) -> dict:
    """B 站元数据探测"""
    if "b23.tv" in url:
        url = _resolve_b23(url)
    m = _BILI_BVID_RE.search(url)
    if not m:
        return {}
    bvid = m.group(1)
    data = _bili_view(bvid)
    return {"title": data.get("title", ""),
            "uploader": (data.get("owner") or {}).get("name", ""),
            "duration": data.get("duration", 0), "id": bvid,
            "webpage_url": f"https://www.bilibili.com/video/{bvid}"}


def bili_extract(url: str, workdir: str, progress_cb=None) -> tuple:
    """
    B 站专用下载：API 直连（yt-dlp 网页路线常被 412 拦截）。
    返回 (wav_path, meta)。分 P 视频取第一 P。
    """
    def _p(p, s):
        if progress_cb:
            progress_cb(p, s)

    _p(3, "解析 B 站链接")
    if "b23.tv" in url:
        url = _resolve_b23(url)
    m = _BILI_BVID_RE.search(url)
    if not m:
        raise ValueError("未从链接中找到 B 站 BV 号")
    bvid = m.group(1)

    _p(8, "获取视频信息")
    data = _bili_view(bvid)
    title = data.get("title", "")
    uploader = (data.get("owner") or {}).get("name", "")
    duration = data.get("duration", 0)
    cid = (data.get("pages") or [{}])[0].get("cid") or data.get("cid")

    ck = _bili_get_cookie()
    headers = _bili_api_headers(ck)

    _p(15, "获取音频流地址")
    r = requests.get("https://api.bilibili.com/x/player/playurl",
                     params={"bvid": bvid, "cid": cid, "fnval": 16},
                     headers=headers, timeout=15)
    pd = r.json().get("data") or {}
    audios = (pd.get("dash") or {}).get("audio") or []
    if not audios:
        raise ValueError("该视频没有可直接下载的音频流（可能是付费/会员内容）")
    audio_url = audios[0]["baseUrl"]

    _p(25, "下载音频")
    m4s = os.path.join(workdir, "bili_audio.m4s")
    with requests.get(audio_url, headers=headers, stream=True,
                      timeout=(10, 120)) as rr:
        rr.raise_for_status()
        total = int(rr.headers.get("Content-Length") or 0)
        done = 0
        with open(m4s, "wb") as f:
            for chunk in rr.iter_content(chunk_size=1 << 18):
                f.write(chunk)
                done += len(chunk)
                if total:
                    _p(25 + int(done / total * 40), "下载音频")

    _p(68, "抽取音轨")
    wav_path = os.path.join(workdir, "audio.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", m4s, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", wav_path, "-loglevel", "error"],
        check=True, timeout=600,
    )
    try:
        os.remove(m4s)
    except OSError:
        pass
    _p(72, "音轨就绪")
    meta = {"title": title, "uploader": uploader, "duration": duration,
            "webpage_url": f"https://www.bilibili.com/video/{bvid}"}
    return wav_path, meta


# ================================================================ yt-dlp 通用路线
def _ytdlp_probe(url: str, cookie_file: str = "") -> dict:
    """yt-dlp 探测元数据（标题/时长/作者）"""
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True,
            "socket_timeout": 20}
    if cookie_file and os.path.exists(cookie_file):
        opts["cookiefile"] = cookie_file
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration") or 0,
        "id": info.get("id") or "",
        "webpage_url": info.get("webpage_url") or url,
    }


def _ytdlp_download(url: str, workdir: str, cookie_file: str = "",
                    progress_cb=None) -> tuple:
    """yt-dlp 下载 + ffmpeg 抽音轨，返回 (wav_path, meta)"""
    import yt_dlp

    def _pct(p, stage):
        if progress_cb:
            progress_cb(p, stage)

    tmp_template = os.path.join(workdir, "media.%(ext)s")

    def _hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                _pct(int(done / total * 60), "下载中")
        elif d.get("status") == "finished":
            _pct(62, "下载完成，抽取音轨")

    # 选格式：优先纯音频；否则最低画质的合流
    fmt = "bestaudio/best[ext=mp4][height<=480]/worst"
    opts = {
        "format": fmt,
        "outtmpl": tmp_template,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
        "progress_hooks": [_hook],
    }
    if cookie_file and os.path.exists(cookie_file):
        opts["cookiefile"] = cookie_file

    _pct(5, "解析链接")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    meta = {
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "duration": info.get("duration") or 0,
        "webpage_url": info.get("webpage_url") or url,
    }

    files = glob.glob(os.path.join(workdir, "media.*"))
    if not files:
        raise RuntimeError("下载完成但未找到媒体文件")
    media_file = max(files, key=os.path.getmtime)

    _pct(65, "抽取音轨")
    wav_path = os.path.join(workdir, "audio.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", media_file, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", wav_path, "-loglevel", "error"],
        check=True, timeout=600,
    )
    # 抽完即删，视频不落盘
    try:
        os.remove(media_file)
    except OSError:
        pass
    _pct(70, "音轨就绪")
    return wav_path, meta


# ================================================================ 统一入口
def probe_info(url: str, cookie_file: str = "") -> dict:
    """统一元数据探测：B 站走 API，其余走 yt-dlp"""
    if _is_bili(url):
        try:
            info = bili_probe(url)
            if info:
                return info
        except Exception as e:
            log.warning("B 站 API 探测失败，回退 yt-dlp: %s", e)
    return _ytdlp_probe(url, cookie_file)


def download_audio(url: str, workdir: str, cookie_file: str = "",
                   progress_cb=None) -> tuple:
    """统一下载：B 站走 API 直连，其余走 yt-dlp"""
    if _is_bili(url):
        try:
            return bili_extract(url, workdir, progress_cb=progress_cb)
        except Exception as e:
            log.warning("B 站 API 下载失败，回退 yt-dlp: %s", e)
    return _ytdlp_download(url, workdir, cookie_file, progress_cb)


if __name__ == "__main__":
    # 自测：python extractor.py "分享文本..."
    import sys
    import json
    import tempfile
    import shutil
    logging.basicConfig(level=logging.INFO)
    text = " ".join(sys.argv[1:]) or "https://v.douyin.com/RCqd3qC/"
    u = extract_url(text)
    print("URL:", u)
    from cookies_manager import get_cookie_file_for
    ck = get_cookie_file_for(u)
    print("cookie:", ck or "(无)")
    info = probe_info(u, ck)
    print(json.dumps(info, ensure_ascii=False, indent=1))
    tmp = tempfile.mkdtemp()
    wav, meta = download_audio(u, tmp, ck,
                               lambda p, s: print(f"  [{p}%] {s}"))
    print("wav:", wav, os.path.getsize(wav), "bytes")
    shutil.rmtree(tmp, ignore_errors=True)
