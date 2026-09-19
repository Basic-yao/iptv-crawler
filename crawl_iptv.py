#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用自己的频道列表驱动，从 sources.txt 抓取公开直播源，
按关键词匹配 -> 可播验证 -> 测速排序 -> 输出可用列表。

输入:
  channels.txt   自己的频道清单 (输出名,关键词1,关键词2,...)
  sources.txt    候选源地址池 (每行一个 http(s) 的 m3u/txt)
输出:
  my_list.m3u    标准 m3u (播放器通用)
  hd.txt         兼容 IPTV 仓库的 "频道名,url" 格式
  report.txt     抓取/匹配/可用 统计报告

设计原则:
- 只抓公开免费源，遵守目标站、加超时与并发限制、不暴力探测
- 验证: 先 HTTP 首段探测；若系统有 ffprobe 则对 m3u8 做深检(可选)
"""
import os
import re
import sys
import asyncio
import subprocess
import logging

import aiohttp

LOG = logging.getLogger("iptv")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES_FILE = os.path.join(HERE, "sources.txt")
CHANNELS_FILE = os.path.join(HERE, "channels.txt")
OUT_M3U = os.path.join(HERE, "my_list.m3u")
OUT_HD = os.path.join(HERE, "hd.txt")
OUT_REPORT = os.path.join(HERE, "report.txt")

CONCURRENCY = 20          # 并发上限
FETCH_TIMEOUT = 25        # 抓取源列表超时(秒)
CHECK_TIMEOUT = 8         # 单地址可播探测超时(秒)
KEEP_PER_CHANNEL = 3      # 每个频道保留前 N 个(按响应速度)

USER_AGENT = "Mozilla/5.0 (compatible; IPTVCrawler/1.0)"


# ---------------- 读取配置 ----------------
def load_list(path):
    """读取非注释非空行"""
    out = []
    if not os.path.exists(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def load_channels(path):
    """返回 {输出名: [关键词...]}"""
    want = {}
    for raw in load_list(path):
        parts = [p.strip() for p in raw.split(",")]
        parts = [p for p in parts if p]
        if not parts:
            continue
        key = parts[0]
        kws = parts[1:] if len(parts) > 1 else [key]
        want[key] = kws
    return want


# ---------------- 解析源为 (name, url) ----------------
def parse_m3u(text):
    items = []
    name = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            m = re.split(r",\s*", line, maxsplit=1)
            name = m[1].strip() if len(m) > 1 else ""
        elif line.startswith("#") or line.startswith("rtp://") or line.startswith("rtsp://"):
            if not line.startswith("#"):
                name = None
        else:
            if re.match(r"^https?://", line):
                if name is not None:
                    items.append((name, line))
                name = None
    return items


def parse_txt(text):
    """兼容 "频道名,http..." 或 "频道名\nurl" 两种简单格式"""
    items = []
    lines = text.splitlines()
    for i, l in enumerate(lines):
        l = l.strip()
        if not l or l.startswith("#"):
            continue
        if re.match(r"^https?://", l):
            if i > 0:
                prev = lines[i - 1].strip()
                if prev and not re.match(r"^https?://", prev) and not prev.startswith("#"):
                    items.append((prev, l))
            continue
        m = re.match(r"^(.*?)[,\s]+\s*(https?://\S+)\s*$", l)
        if m:
            items.append((m.group(1).strip(), m.group(2).strip()))
    return items


def is_m3u_text(head):
    return "#EXTM3U" in head or ".m3u" in head.lower()


async def fetch_source(session, url):
    """抓取一个源地址，返回解析后的 (name,url) 列表"""
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT),
            headers={"User-Agent": USER_AGENT},
        ) as r:
            if r.status != 200:
                LOG.warning("源 %s 返回 %s", url, r.status)
                return []
            data = await r.text(limit=5_000_000)
    except Exception as e:
        LOG.warning("抓取源失败 %s: %s", url, e)
        return []

    head = data[:200]
    if is_m3u_text(head):
        return parse_m3u(data)
    return parse_txt(data)


# ---------------- 关键词匹配 ----------------
def match_channel(item_name, keywords):
    n = (item_name or "").lower()
    for kw in keywords:
        if kw and kw.lower() in n:
            return True
    return False


# ---------------- 可播验证 ----------------
def ffprobe_check(url):
    """若系统有 ffprobe，对 m3u8 做深检，返回 bool 或 None(不可用ffprobe)"""
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True
        )
    except Exception:
        return None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=format_name",
             "-show_streams", "-timeout", "15000000", url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25,
        )
        out = r.stdout + r.stderr
        return ("Stream #" in out) or ("format_name" in out)
    except Exception:
        return False


async def check(session, name, url):
    """返回 (name, url, cost_ms) 或 None"""
    started = asyncio.get_event_loop().time()
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=CHECK_TIMEOUT),
            headers={"User-Agent": USER_AGENT},
        ) as r:
            if r.status != 200:
                return None
            if url.lower().endswith(".m3u8") or "mpegurl" in r.headers.get("Content-Type", "").lower():
                data = await r.text(limit=4096)
                if "#EXTM3U" not in data:
                    return None
                ok = ffprobe_check(url)
                if ok is False:
                    return None
                cost = int((asyncio.get_event_loop().time() - started) * 1000)
                return (name, url, cost)
            total = 0
            async for chunk in r.content.iter_chunked(8192):
                total += len(chunk)
                if total >= 32768:
                    break
            if total == 0:
                return None
            cost = int((asyncio.get_event_loop().time() - started) * 1000)
            return (name, url, cost)
    except Exception:
        return None


# ---------------- 主流程 ----------------
async def main():
    want = load_channels(CHANNELS_FILE)
    sources = [u for u in load_list(SOURCES_FILE) if re.match(r"^https?://", u)]
    if not want:
        LOG.error("channels.txt 为空，请先填写你想要的频道")
        sys.exit(1)
    if not sources:
        LOG.error("sources.txt 没有可用的 http(s) 源地址")
        sys.exit(1)

    LOG.info("频道需求: %d 个, 源地址池: %d 个", len(want), len(sources))

    all_items = []
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as s:
        tasks = [fetch_source(s, u) for u in sources]
        for coro in asyncio.as_completed(tasks):
            res = await coro
            all_items.extend(res)
    LOG.info("共解析到候选条目: %d", len(all_items))

    selected = {}
    for name, url in all_items:
        for out_name, kws in want.items():
            if match_channel(name, kws):
                selected.setdefault(out_name, set()).add(url)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=CONCURRENCY)) as s:
        async def bounded(item):
            async with sem:
                out_name, url = item
                return await check(s, out_name, url)

        tasks = [bounded((n, u)) for n, urls in selected.items() for u in urls]
        checked = await asyncio.gather(*tasks)

    final = {}
    for ok in checked:
        if not ok:
            continue
        n, u, cost = ok
        final.setdefault(n, []).append((u, cost))
    for n in final:
        final[n].sort(key=lambda x: x[1])
        final[n] = final[n][:KEEP_PER_CHANNEL]

    write_outputs(selected, final)

    lines = [
        f"候选源数量: {len(sources)}",
        f"解析候选条目: {len(all_items)}",
        f"匹配到的频道数: {len(selected)} / 需求 {len(want)}",
        f"验证可用频道数: {len(final)}",
    ]
    for n in want:
        got = len(final.get(n, []))
        lines.append(f"  - {n}: {'可用 ' + str(got) + ' 个源' if got else '未找到匹配'}")
    report = "\n".join(lines)
    with open(OUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    LOG.info("\n%s", report)


def write_outputs(selected, final):
    with open(OUT_M3U, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for n, lst in final.items():
            for u, cost in lst:
                f.write(f'#EXTINF:-1 group-title="auto",{n}\n{u}\n')

    with open(OUT_HD, "w", encoding="utf-8") as f:
        for n, lst in final.items():
            for u, _ in lst:
                f.write(f"{n},{u}\n")

    LOG.info("已写出: %s, %s", OUT_M3U, OUT_HD)


if __name__ == "__main__":
    asyncio.run(main())
