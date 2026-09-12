# -*- coding: utf-8 -*-
"""
batch.py — 批量转写脚本

用法:
    python tools/batch.py links.txt              # links.txt 每行一条链接或分享文本
    python tools/batch.py links.txt -o out/      # 指定输出目录

输出: 每条链接一个 .txt（标题+作者+时长+带时间戳转写），外加汇总 all.md
"""
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import extractor
import transcriber
from cookies_manager import get_cookie_file_for


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="链接列表文件，每行一条（链接或整段分享文本）")
    ap.add_argument("-o", "--out", default="batch_out", help="输出目录")
    args = ap.parse_args()

    with open(args.file, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    os.makedirs(args.out, exist_ok=True)
    print(f"共 {len(lines)} 条任务\n")

    summary = ["# 批量转写汇总", f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}", ""]
    ok = 0
    for i, line in enumerate(lines, 1):
        url = extractor.extract_url(line)
        if not url:
            print(f"[{i}/{len(lines)}] ✗ 未找到链接: {line[:40]}")
            continue
        print(f"[{i}/{len(lines)}] {url}")
        try:
            t0 = time.time()
            ck = get_cookie_file_for(url)
            import tempfile, shutil
            tmp = tempfile.mkdtemp()
            wav, meta = extractor.download_audio(url, tmp, ck)
            result = transcriber.transcribe(wav)
            shutil.rmtree(tmp, ignore_errors=True)

            safe_title = (meta["title"] or url).replace("/", "_")[:50]
            body = "\n".join(
                f"[{int(s['start']//60):02d}:{int(s['start']%60):02d}] {s['text']}"
                for s in result["segments"])
            out_path = os.path.join(args.out, f"{i:02d}_{safe_title}.txt")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(f"标题: {meta['title']}\n作者: {meta['uploader']}\n"
                        f"时长: {meta['duration']}s\n链接: {meta['webpage_url']}\n"
                        + "─" * 30 + "\n\n" + body)
            print(f"    ✓ {len(result['segments'])} 段 / {meta['duration']}s "
                  f"/ {time.time()-t0:.0f}s → {out_path}")
            summary.append(f"## {meta['title']}\n\n{result['full_text']}\n")
            ok += 1
        except Exception as e:
            print(f"    ✗ 失败: {str(e)[:120]}")

    with open(os.path.join(args.out, "all.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary))
    print(f"\n完成: {ok}/{len(lines)} 成功，输出目录: {args.out}/")


if __name__ == "__main__":
    main()
