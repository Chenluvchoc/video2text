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

# 排除：空白、中西文标点、CJK 汉字（\u4e00-\u9fff）、CJK 标点（\u3000-\u303f）、全角符号（\uff01-\uff5e）
# —— 分享文本里链接后面常紧跟中文（"…/xxx复制此链接"），必须把汉字当边界
URL_RE = re.compile(
    r'https?://[^\s，,。；;！!？?"\'（）()\[\]【】<>'
    r'\u4e00-\u9fff\u3000-\u303f\uff01-\uff5e]+')

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


def extract_urls(text: str) -> list:
    """从文本中提取所有 URL（保序去重，链接末尾统一补 /）"""
    if not text:
        return []
    seen, out = set(), []
    for m in URL_RE.finditer(text):
        u = m.group(0)
        if not u.endswith("/"):
            u += "/"
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# ------------------------------------------------ Word 文档链接提取
_WT_RE = re.compile(r'<w:t[^>]*>([^<]*)</w:t>')


def docx_extract_urls(path: str) -> list:
    """
    从 .docx 提取所有链接（保序去重）。

    覆盖两种形态：
      1. 正文/表格里的纯文本 URL（拼接所有 <w:t> 后正则提取）
      2. 超链接按钮（目标 URL 存在 word/_rels/document.xml.rels 的 External Target 里）
    """
    import zipfile
    from xml.sax.saxutils import unescape

    urls = []
    with zipfile.ZipFile(path) as z:
        # 1) 正文 + 表格中的纯文本
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
        text = unescape("".join(_WT_RE.findall(xml)))
        urls += URL_RE.findall(text)
        # 2) 超链接形式的 target
        try:
            rels = z.read("word/_rels/document.xml.rels").decode("utf-8", "ignore")
            urls += re.findall(r'Target="(http[^"]+)"', rels)
        except KeyError:
            pass

    seen, out = set(), []
    for u in urls:
        u = u if u.endswith("/") else u + "/"
        # 跳过 Word 自动识别产生的锚点/书签链接
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


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


# ------------------------------------------------ B 站收藏夹
_FAV_FID_RE = re.compile(r'fid=(\d+)')


def parse_favlist_url(text: str):
    """
    从文本/URL 中解析收藏夹信息。
    支持格式:
      - https://space.bilibili.com/{mid}/favlist?fid=xxx
      - https://www.bilibili.com/medialist/play/ml{fid} （稍后跳转解析）
      - 纯数字 fid
    返回 (fid, mid) ；解析不到返回 (None, None)
    """
    url = extract_url(text) or ""
    if "b23.tv" in url:
        url = _resolve_b23(url)
    m = _FAV_FID_RE.search(url)
    fid = m.group(1) if m else (text.strip() if text.strip().isdigit() else None)
    mid = None
    m2 = re.search(r'space\.bilibili\.com/(\d+)', url)
    if m2:
        mid = m2.group(1)
    if not fid and "ml" in url:
        m3 = re.search(r'ml(\d+)', url)
        if m3:
            fid = m3.group(1)
    return fid, mid


def favlist_items(fid: str, max_items: int = 50) -> list:
    """
    拉取公开收藏夹的视频列表（分页）。
    返回 [{bvid, title, duration, upper}]。私密的收藏夹会抛异常。
    """
    ck = _bili_get_cookie()
    headers = _bili_api_headers(ck)
    items, pn = [], 1
    while len(items) < max_items:
        r = requests.get(
            "https://api.bilibili.com/x/v3/fav/resource/list",
            params={"media_id": fid, "pn": pn, "ps": 20, "keyword": "",
                    "order": "mtime", "type": 0, "tid": 0, "platform": "web"},
            headers=headers, timeout=15)
        j = r.json()
        code = j.get("code", -1)
        if code == -403:
            raise ValueError("该收藏夹是私密的，仅支持公开收藏夹")
        if code != 0:
            raise ValueError(f"B 站收藏夹接口返回错误 code={code} {j.get('message','')}")
        data = j.get("data") or {}
        medias = data.get("medias") or []
        if not medias:
            break
        for mv in medias:
            items.append({
                "bvid": mv.get("bv_id") or mv.get("bvid") or "",
                "title": mv.get("title") or "",
                "duration": mv.get("duration") or 0,
                "upper": (mv.get("upper") or {}).get("name", ""),
            })
        total = data.get("info", {}).get("media_count", 0)
        if len(items) >= total:
            break
        pn += 1
    return items[:max_items]


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
