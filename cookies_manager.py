# -*- coding: utf-8 -*-
"""
cookies_manager.py — 抖音/B站 cookies 自动获取与刷新

抖音网页接口需要"新鲜的 cookies"（不必登录），否则 yt-dlp 解析会报错：
    ERROR: [Douyin] xxx: Fresh cookies (not necessarily logged in) are needed

原理：用无头浏览器访问一次抖音/B站首页，让平台的 JS 种下初始 cookie
（ttwid / __ac_signature 等），然后导出为 Netscape 格式供 yt-dlp 使用。
"""
import os
import time
import logging

log = logging.getLogger("cookies")

COOKIES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies")
os.makedirs(COOKIES_DIR, exist_ok=True)

DY_COOKIE_FILE = os.path.join(COOKIES_DIR, "douyin_cookies.txt")
BILI_COOKIE_FILE = os.path.join(COOKIES_DIR, "bili_cookies.txt")

# cookie 最大有效期（超过就刷新），抖音风控变化快，保守设 6 小时
COOKIE_TTL = 6 * 3600

PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _export_netscape(cookies: list, path: str):
    """Playwright cookies 列表 → Netscape 格式文件（yt-dlp 可用）"""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        if not c.get("name"):
            continue
        dom = c["domain"] if c["domain"].startswith(".") else "." + c["domain"]
        exp = int(c.get("expires", 0) or 0)
        if exp <= 0:
            exp = 2147483647
        sec = "TRUE" if c.get("secure") else "FALSE"
        lines.append(f'{dom}\tTRUE\t{c["path"]}\t{sec}\t{exp}\t{c["name"]}\t{c["value"]}')
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _fetch_site_cookies(home_url: str, wait_ms: int = 6000):
    """无头浏览器访问首页，等待 JS 种 cookie 后导出"""
    from playwright.sync_api import sync_playwright  # 延迟导入，非必需依赖

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(user_agent=PC_UA,
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        try:
            page.goto(home_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            log.warning("访问 %s 出现异常（通常不影响拿 cookie）: %s", home_url, e)
        page.wait_for_timeout(wait_ms)
        cookies = ctx.cookies()
        browser.close()
        return cookies


def refresh_douyin_cookies(force: bool = False) -> str:
    """获取/刷新抖音 cookies，返回 cookie 文件路径"""
    if not force and os.path.exists(DY_COOKIE_FILE):
        age = time.time() - os.path.getmtime(DY_COOKIE_FILE)
        if age < COOKIE_TTL:
            return DY_COOKIE_FILE
    log.info("正在刷新抖音 cookies …")
    cookies = _fetch_site_cookies("https://www.douyin.com/")
    _export_netscape(cookies, DY_COOKIE_FILE)
    log.info("抖音 cookies 已保存: %s（%d 条）", DY_COOKIE_FILE, len(cookies))
    return DY_COOKIE_FILE


def refresh_bili_cookies(force: bool = False) -> str:
    """获取/刷新 B 站 cookies（可选，B 站部分视频需要）"""
    if not force and os.path.exists(BILI_COOKIE_FILE):
        age = time.time() - os.path.getmtime(BILI_COOKIE_FILE)
        if age < COOKIE_TTL:
            return BILI_COOKIE_FILE
    try:
        cookies = _fetch_site_cookies("https://www.bilibili.com/", wait_ms=4000)
        _export_netscape(cookies, BILI_COOKIE_FILE)
        log.info("B 站 cookies 已保存: %s", BILI_COOKIE_FILE)
        return BILI_COOKIE_FILE
    except Exception as e:
        log.warning("B 站 cookies 获取失败（可继续使用）: %s", e)
        return ""


def get_cookie_file_for(url: str) -> str:
    """根据 URL 判断需要哪个 cookie 文件，返回路径或空字符串"""
    u = url.lower()
    if "douyin" in u:
        try:
            return refresh_douyin_cookies()
        except Exception as e:
            # playwright 不可用或网络异常：若存在旧 cookie 则退回使用
            log.warning("抖音 cookies 自动刷新失败: %s", e)
            if os.path.exists(DY_COOKIE_FILE):
                return DY_COOKIE_FILE
            return ""
    if "bilibili" in u or "b23.tv" in u:
        try:
            return refresh_bili_cookies()
        except Exception:
            return os.path.join(COOKIES_DIR, "bili_cookies.txt") if os.path.exists(BILI_COOKIE_FILE) else ""
    return ""


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print("douyin:", refresh_douyin_cookies(force=True))
    print("bilibili:", refresh_bili_cookies(force=True))
