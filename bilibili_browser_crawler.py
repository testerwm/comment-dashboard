#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from functools import reduce
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError, async_playwright


BASE_URL = "https://www.bilibili.com"
API_URL = "https://api.bilibili.com"
LOGIN_URL = "https://passport.bilibili.com/login"
DEFAULT_PROFILE_DIR = Path(".bilibili-profile")
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def delay(min_seconds: float = 1.0, max_seconds: float = 2.0) -> None:
    time.sleep(random.uniform(min_seconds, max_seconds))


def get_mixin_key(raw: str) -> str:
    return reduce(lambda s, i: s + raw[i], MIXIN_KEY_ENC_TAB, "")[:32]


def enc_wbi(params: dict[str, Any], img_key: str, sub_key: str) -> dict[str, Any]:
    mixin_key = get_mixin_key(img_key + sub_key)
    signed = dict(params)
    signed["wts"] = int(time.time())
    query = "&".join(
        f"{k}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(signed.items())
    )
    signed["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return signed


def build_cookie_header(cookies: list[dict[str, Any]]) -> str:
    useful = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value:
            useful.append(f"{name}={value}")
    return "; ".join(useful)


def has_bilibili_login(cookies: list[dict[str, Any]]) -> bool:
    return any(cookie.get("name") == "SESSDATA" and cookie.get("value") for cookie in cookies)


def cleanup_stale_chrome_locks(profile_dir: Path) -> None:
    lock = profile_dir / "SingletonLock"
    if not lock.is_symlink():
        return
    target = os.readlink(lock)
    match = re.search(r"-(\d+)$", target)
    if not match:
        return
    pid = int(match.group(1))
    try:
        os.kill(pid, 0)
        return
    except ProcessLookupError:
        pass
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie", "RunningChromeVersion"):
        path = profile_dir / name
        try:
            path.unlink()
        except FileNotFoundError:
            pass


async def login(profile_dir: Path, auto: bool = False, timeout: int = 180) -> None:
    cleanup_stale_chrome_locks(profile_dir)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1360, "height": 900},
            locale="zh-CN",
        )
        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        cookies = await context.cookies([BASE_URL, API_URL])
        if has_bilibili_login(cookies):
            print("检测到当前 Bilibili profile 已有登录状态。如需更换账号，请在浏览器中退出后重新登录。", flush=True)
        else:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        if auto:
            print("请在打开的浏览器中登录 Bilibili。检测到登录成功后会自动保存状态。", flush=True)
            deadline = time.time() + timeout
            while time.time() < deadline:
                cookies = await context.cookies([BASE_URL, API_URL])
                if has_bilibili_login(cookies):
                    print("已检测到 Bilibili 登录状态，保存完成。", flush=True)
                    await context.close()
                    return
                await asyncio.sleep(2)
            print("未能自动确认登录状态，请在网站点击“兜底保存登录状态”。", flush=True)
            input()
        else:
            print("请在打开的浏览器中登录 Bilibili。登录完成后回到网站点击“保存登录状态”。", flush=True)
            input()
            cookies = await context.cookies([BASE_URL, API_URL])
            if has_bilibili_login(cookies):
                print("Bilibili 登录状态保存成功。", flush=True)
            else:
                print("未检测到 Bilibili 登录状态，请确认是否登录成功。", flush=True)
        await context.close()


async def read_cookie_header(profile_dir: Path, headless: bool) -> str:
    cleanup_stale_chrome_locks(profile_dir)
    try:
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                str(profile_dir),
                headless=headless,
                viewport={"width": 1360, "height": 900},
                locale="zh-CN",
            )
            page = await context.new_page()
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(1)
            cookies = await context.cookies([BASE_URL, API_URL])
            await context.close()
    except PlaywrightError as exc:
        raise RuntimeError(
            f"无法读取 Bilibili 登录状态，通常是登录窗口还没关闭或 profile 被占用。"
            f"请先点击“保存登录状态”，或点击“停止当前任务”后重试。原始错误：{exc}"
        ) from exc
    return build_cookie_header(cookies)


def api_get(url: str, params: dict[str, Any] | None, cookie_header: str, tag: str) -> dict[str, Any] | None:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
            "Referer": BASE_URL,
            "Origin": BASE_URL,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cookie": cookie_header,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except Exception as exc:
        print(f"  [{tag}] 请求失败：{type(exc).__name__}: {exc}", flush=True)
        return None


def get_wbi_keys(cookie_header: str) -> tuple[str, str] | None:
    data = api_get(f"{API_URL}/x/web-interface/nav", None, cookie_header, "WBI")
    if not data or data.get("code") != 0:
        print(f"  [WBI] 获取失败：{(data or {}).get('message')}", flush=True)
        return None
    wbi_img = data["data"]["wbi_img"]
    img_key = wbi_img["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = wbi_img["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    return img_key, sub_key


def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def search_videos(keyword: str, video_count: int, cookie_header: str, order: str, duration: int) -> list[dict[str, Any]]:
    keys = get_wbi_keys(cookie_header)
    videos: list[dict[str, Any]] = []
    page = 1
    while len(videos) < video_count and page <= 20:
        params = {
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "page_size": 20,
            "order": order,
        }
        if duration:
            params["duration"] = duration
        signed = enc_wbi(params, *keys) if keys else params
        data = api_get(f"{API_URL}/x/web-interface/wbi/search/type", signed, cookie_header, f"搜索第{page}页")
        if not data or data.get("code") != 0:
            print(f"  [搜索] 返回异常：{(data or {}).get('code')} {(data or {}).get('message')}", flush=True)
            break
        results = ((data.get("data") or {}).get("result") or [])
        if not results:
            break
        for item in results:
            if len(videos) >= video_count:
                break
            videos.append({
                "bvid": item.get("bvid", ""),
                "aid": item.get("aid", 0),
                "title": clean_html(item.get("title", "")),
                "author": item.get("author", ""),
                "play": item.get("play", 0),
                "danmaku": item.get("video_review", 0),
                "comment": item.get("review", 0),
                "favorites": item.get("favorites", 0),
                "duration": item.get("duration", ""),
                "pubdate": item.get("pubdate", 0),
                "pubdate_str": datetime.fromtimestamp(item.get("pubdate", 0)).strftime("%Y-%m-%d") if item.get("pubdate") else "",
                "description": item.get("description", ""),
                "tag": item.get("tag", ""),
                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
            })
        print(f"[搜索] 第{page}页获得 {len(results)} 个结果，已取 {len(videos)}/{video_count}", flush=True)
        page += 1
        delay()
    return videos


def parse_reply(reply: dict[str, Any]) -> dict[str, Any]:
    member = reply.get("member", {})
    content = reply.get("content", {})
    ctime = reply.get("ctime", 0)
    return {
        "rpid": reply.get("rpid"),
        "parent": reply.get("parent", 0),
        "root": reply.get("root", 0),
        "uid": member.get("mid"),
        "username": member.get("uname", ""),
        "sex": member.get("sex", ""),
        "level": member.get("level_info", {}).get("current_level"),
        "vip_type": member.get("vip", {}).get("vipType", 0),
        "location": reply.get("reply_control", {}).get("location", ""),
        "message": content.get("message", ""),
        "like": reply.get("like", 0),
        "rcount": reply.get("rcount", 0),
        "ctime": ctime,
        "ctime_str": datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S") if ctime else "",
        "replies": [],
    }


def get_hot_comments(cookie_header: str, aid: int, bvid: str, comment_count: int) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    next_offset = 0
    page = 1
    while len(comments) < comment_count and page <= 20:
        data = api_get(
            f"{API_URL}/x/v2/reply/main",
            {"type": 1, "oid": aid, "mode": 3, "ps": min(20, max(comment_count, 1)), "next": next_offset},
            cookie_header,
            f"{bvid}热评第{page}页",
        )
        if not data or data.get("code") != 0:
            print(f"  [评论] 返回异常：{(data or {}).get('code')} {(data or {}).get('message')}", flush=True)
            break
        reply_data = data.get("data") or {}
        replies = reply_data.get("replies") or []
        if not replies:
            break
        for reply in replies:
            if len(comments) >= comment_count:
                break
            comment = parse_reply(reply)
            for preview in reply.get("replies") or []:
                comment["replies"].append(parse_reply(preview))
            comments.append(comment)
        cursor = reply_data.get("cursor") or {}
        next_offset = cursor.get("next", 0)
        print(f"  [热评] {bvid} 第{page}页，主评论累计 {len(comments)}/{comment_count}", flush=True)
        if cursor.get("is_end", True) or not next_offset:
            break
        page += 1
        delay()
    return sorted(comments, key=lambda c: c.get("like", 0), reverse=True)[:comment_count]


def get_sub_replies(cookie_header: str, aid: int, root_rpid: int, known_ids: set[int], max_replies: int) -> list[dict[str, Any]]:
    if max_replies <= 0:
        return []
    collected: list[dict[str, Any]] = []
    page = 1
    while len(collected) < max_replies and page <= 50:
        data = api_get(
            f"{API_URL}/x/v2/reply/reply",
            {"type": 1, "oid": aid, "root": root_rpid, "ps": 20, "pn": page},
            cookie_header,
            f"子回复root={root_rpid}",
        )
        if not data or data.get("code") != 0:
            break
        replies = ((data.get("data") or {}).get("replies") or [])
        if not replies:
            break
        for reply in replies:
            parsed = parse_reply(reply)
            if parsed["rpid"] not in known_ids:
                collected.append(parsed)
                known_ids.add(parsed["rpid"])
            if len(collected) >= max_replies:
                break
        page += 1
        delay(0.6, 1.2)
    return collected


def save_result(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  [保存] {path}", flush=True)


async def crawl(args: argparse.Namespace) -> None:
    cookie_header = await read_cookie_header(args.profile_dir, args.headless)
    if "SESSDATA=" not in cookie_header:
        print("警告：当前 Bilibili 登录状态可能未保存或已经失效。", flush=True)
    videos = search_videos(args.keyword, args.video_count, cookie_header, args.order, args.duration)
    result = {
        "platform": "bilibili",
        "keyword": args.keyword,
        "crawl_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_videos": len(videos),
        "comment_mode": "hot",
        "comments_per_video": args.comment_count,
        "filters": {
            "order": args.order,
            "duration": args.duration,
        },
        "videos": [],
    }
    for index, video in enumerate(videos, 1):
        aid = int(video.get("aid") or 0)
        bvid = video.get("bvid", "")
        print(f"\n[{index}/{len(videos)}] {video.get('title')} | {bvid}", flush=True)
        comments = get_hot_comments(cookie_header, aid, bvid, args.comment_count) if aid else []
        for comment in comments:
            known_ids = {reply["rpid"] for reply in comment["replies"] if reply.get("rpid")}
            known_ids.add(comment["rpid"])
            if comment.get("rcount", 0) > len(comment["replies"]):
                extras = get_sub_replies(cookie_header, aid, int(comment["rpid"]), known_ids, args.max_replies_per_comment)
                comment["replies"].extend(extras)
        total = len(comments) + sum(len(c["replies"]) for c in comments)
        video["comments"] = comments
        video["comment_count_crawled"] = len(comments)
        video["total_count_crawled"] = total
        result["videos"].append(video)
        save_result(args.output, result)
        delay()
    print(f"\n完成，保存到：{args.output}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bilibili 登录态热门视频热评爬虫")
    parser.add_argument("--login", action="store_true", help="打开浏览器登录 Bilibili")
    parser.add_argument("--auto-login", action="store_true", help="自动检测登录成功后退出")
    parser.add_argument("--login-timeout", type=int, default=180)
    parser.add_argument("--keyword", default="首尔病")
    parser.add_argument("--video-count", type=int, default=10)
    parser.add_argument("--order", default="totalrank", choices=["totalrank", "click", "pubdate", "dm", "stow"])
    parser.add_argument("--duration", type=int, default=0, choices=[0, 1, 2, 3, 4])
    parser.add_argument("--comment-count", type=int, default=20, help="每个视频抓取的最热主评论数量")
    parser.add_argument("--max-replies-per-comment", type=int, default=200, help="每条热评最多抓取多少条回复")
    parser.add_argument("--output", type=Path, default=Path("bilibili_comments.json"))
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    if args.login:
        asyncio.run(login(args.profile_dir, args.auto_login, args.login_timeout))
    else:
        asyncio.run(crawl(args))


if __name__ == "__main__":
    main()
