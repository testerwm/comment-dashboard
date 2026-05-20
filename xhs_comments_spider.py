#!/usr/bin/env python3
"""
小红书关键词评论爬虫。

用法：
  python xhs_comments_spider.py --login
  python xhs_comments_spider.py --keyword "首尔病" --limit 10 --output xhs_seoul_sick_comments.json

说明：
  - 使用 Playwright 持久化浏览器 profile，首次运行 --login 后复用登录态。
  - 小红书 DOM 经常变化，本脚本尽量用多组选择器兜底。
  - 评论点赞数用于计算每个帖子下 top_10_comments。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from playwright.async_api import Locator, Page, TimeoutError, async_playwright


BASE_URL = "https://www.xiaohongshu.com"
DEFAULT_PROFILE_DIR = Path(".xhs-profile")


@dataclass
class Comment:
    comment_id: str
    author: str
    content: str
    like_count: int
    time_text: str
    location: str = ""
    raw_text: str = ""
    replies: list["Comment"] = field(default_factory=list)


@dataclass
class Post:
    rank: int
    note_id: str
    url: str
    title: str = ""
    preview_text: str = ""
    author: str = ""
    like_count: int = 0
    comment_count_text: str = ""
    note_type: str = ""
    scrape_error: str = ""
    comments_count: int = 0
    reply_count: int = 0
    total_count_crawled: int = 0
    comments: list[Comment] = field(default_factory=list)
    top_10_comments: list[Comment] = field(default_factory=list)
    top_comments: list[Comment] = field(default_factory=list)


def parse_count(text: str | None) -> int:
    if not text:
        return 0
    s = text.strip().replace(",", "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([万wWkK]?)", s)
    if not match:
        return 0
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"万", "w"}:
        value *= 10000
    elif unit == "k":
        value *= 1000
    return int(value)


def note_id_from_url(url: str) -> str:
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if parts:
        return parts[-1]
    return re.sub(r"\W+", "_", url)[-80:]


async def human_pause(min_seconds: float = 0.6, max_seconds: float = 1.6) -> None:
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))


async def wait_for_optional_network_idle(page: Page, timeout: int = 8000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except TimeoutError:
        pass


async def first_text(root: Locator, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            loc = root.locator(selector).first
            if await loc.count() > 0:
                text = (await loc.inner_text(timeout=1500)).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


async def combined_text(root: Locator, selectors: list[str]) -> str:
    parts: list[str] = []
    for selector in selectors:
        try:
            loc = root.locator(selector)
            count = await loc.count()
            for index in range(min(count, 5)):
                text = (await loc.nth(index).inner_text(timeout=1000)).strip()
                if text and text not in parts:
                    parts.append(text)
        except Exception:
            continue
    return "\n".join(parts)


async def locator_value(locator: Locator) -> str:
    try:
        return (await locator.input_value(timeout=1000)).strip()
    except Exception:
        try:
            return (await locator.get_attribute("value", timeout=1000) or "").strip()
        except Exception:
            return ""


def keyword_matches(text: str, keyword: str) -> bool:
    compact_text = re.sub(r"\s+", "", text or "").lower()
    compact_keyword = re.sub(r"\s+", "", keyword or "").lower()
    return bool(compact_keyword and compact_keyword in compact_text)


def looks_like_xhs_login(cookies: list[dict[str, Any]]) -> bool:
    cookie_map = {cookie.get("name"): cookie.get("value") for cookie in cookies}
    return bool(
        cookie_map.get("web_session")
        or cookie_map.get("access-token")
        or cookie_map.get("customer-sso-sid")
    )


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
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
        )
        page = await context.new_page()
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        if auto:
            print("请在打开的浏览器里手动登录小红书。检测到登录成功后会自动保存状态。", flush=True)
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                cookies = await context.cookies([BASE_URL])
                if looks_like_xhs_login(cookies):
                    print("已检测到小红书登录状态，保存完成。", flush=True)
                    await context.close()
                    return
                await asyncio.sleep(2)
            print("未能自动确认登录状态，请在网站点击“兜底保存登录状态”。", flush=True)
            input()
        else:
            print("请在打开的浏览器里手动登录小红书。登录完成后回到网站点击“我已登录，保存状态”。")
            input()
        await context.close()


async def click_text_option(page: Page, labels: list[str], label_name: str) -> bool:
    for label in labels:
        selectors = [
            f"text=/^\\s*{re.escape(label)}\\s*$/",
            f"[role=button]:has-text('{label}')",
            f"button:has-text('{label}')",
            f"span:has-text('{label}')",
            f"div:has-text('{label}')",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                for index in range(await loc.count()):
                    candidate = loc.nth(index)
                    if not await candidate.is_visible(timeout=800):
                        continue
                    await candidate.click(timeout=2000)
                    await human_pause(0.8, 1.5)
                    print(f"[筛选] {label_name}：{label}", flush=True)
                    return True
            except Exception:
                continue
    return False


async def open_filter_panel(page: Page) -> bool:
    if await page.locator("text=排序依据").count() > 0:
        return True
    try:
        filter_button = page.locator(".filter:has-text('筛选')").last
        if await filter_button.count() > 0 and await filter_button.is_visible(timeout=1000):
            await filter_button.hover(timeout=2500)
            await human_pause(0.5, 1.0)
            if await page.locator("text=排序依据").count() > 0:
                print("[筛选] 已悬停打开筛选面板", flush=True)
                return True
    except Exception:
        pass
    triggers = [
        "text=/^\\s*筛选\\s*$/",
        "text=/^\\s*排序\\s*$/",
        "[aria-label*='筛选']",
        "[class*='filter']",
        "button:has-text('筛选')",
        "div:has-text('筛选')",
    ]
    for selector in triggers:
        try:
            loc = page.locator(selector)
            for index in range(await loc.count()):
                candidate = loc.nth(index)
                if not await candidate.is_visible(timeout=1000):
                    continue
                await candidate.hover(timeout=2500)
                await human_pause(0.8, 1.5)
                if await page.locator("text=排序依据").count() > 0:
                    print("[筛选] 已打开筛选面板", flush=True)
                    return True
                await candidate.click(timeout=2500)
                await human_pause(0.8, 1.5)
                if await page.locator("text=排序依据").count() > 0:
                    print("[筛选] 已打开筛选面板", flush=True)
                    return True
        except Exception:
            continue
    print("[筛选] 未找到筛选面板入口，将尝试直接点击筛选项", flush=True)
    return False


async def click_filter_panel_tag(page: Page, labels: list[str], label_name: str) -> bool:
    if not await open_filter_panel(page):
        return await click_text_option(page, labels, label_name)
    for label in labels:
        try:
            rect = await page.evaluate(
                """
                (label) => {
                  const options = [...document.querySelectorAll('.filter-panel .tags')]
                    .filter((el) => (el.innerText || el.textContent || '').trim() === label);
                  const el = options[options.length - 1];
                  if (!el) return null;
                  const rect = el.getBoundingClientRect();
                  return { x: rect.x, y: rect.y, w: rect.width, h: rect.height };
                }
                """,
                label,
            )
            if not rect:
                continue
            await page.mouse.click(rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2)
            await human_pause(1.0, 1.8)
            print(f"[筛选] {label_name}：{label}", flush=True)
            is_active = await page.evaluate(
                """
                (label) => [...document.querySelectorAll('.filter-panel .tags.active')]
                  .some((el) => (el.innerText || el.textContent || '').trim() === label)
                """,
                label,
            )
            if is_active:
                print(f"[筛选] 已确认选中：{label}", flush=True)
            else:
                print(f"[筛选] 已点击但未确认选中：{label}", flush=True)
            return True
        except Exception:
            continue
    return await click_text_option(page, labels, label_name)


async def close_filter_panel(page: Page) -> None:
    try:
        await page.mouse.move(300, 230)
        await human_pause(0.4, 0.8)
    except Exception:
        pass


async def open_search(page: Page, keyword: str, content_type: str, sort: str) -> None:
    print(f"[搜索] 打开小红书搜索页，关键词：{keyword}", flush=True)
    url = f"{BASE_URL}/search_result?keyword={quote(keyword)}"
    await page.goto(url, wait_until="domcontentloaded")
    await wait_for_optional_network_idle(page)
    await human_pause(1.2, 2.2)

    search_selectors = [
        "input[type='search']",
        "input[placeholder*='搜索']",
        "input",
        "[contenteditable='true']",
    ]
    for selector in search_selectors:
        try:
            search = page.locator(selector).first
            if await search.count() == 0 or not await search.is_visible(timeout=1000):
                continue
            current = await locator_value(search)
            if keyword_matches(current, keyword):
                break
            await search.click(timeout=2000)
            try:
                await search.fill(keyword, timeout=2000)
            except Exception:
                await page.keyboard.press("Meta+A")
                await page.keyboard.type(keyword)
            await page.keyboard.press("Enter")
            await wait_for_optional_network_idle(page)
            await human_pause(1.8, 3.0)
            print("[搜索] 已通过页面搜索框提交关键词", flush=True)
            break
        except Exception:
            continue

    type_labels = {
        "all": ["全部"],
        "image": ["图文"],
        "video": ["视频"],
    }
    sort_labels = {
        "general": ["综合"],
        "latest": ["最新"],
        "most_liked": ["最多点赞"],
        "most_commented": ["最多评论"],
        "most_collected": ["最多收藏"],
        "hot": ["最多点赞", "最热"],
    }
    if sort in sort_labels:
        clicked = await click_filter_panel_tag(page, sort_labels[sort], "排序")
        if not clicked:
            print(f"[筛选] 未能点击排序：{sort}", flush=True)
    if content_type in type_labels and content_type != "all":
        clicked = await click_filter_panel_tag(page, type_labels[content_type], "内容类型")
        if not clicked:
            print(f"[筛选] 未能点击内容类型：{content_type}", flush=True)
    await close_filter_panel(page)
    await wait_for_optional_network_idle(page)
    await human_pause(1.5, 2.5)


async def collect_posts(page: Page, limit: int, keyword: str) -> list[Post]:
    posts_by_url: dict[str, Post] = {}
    previous_count = -1
    stable_rounds = 0

    filtered_count = 0
    while len(posts_by_url) < limit and stable_rounds < 10:
        cards = page.locator("section.note-item, div.note-item, [class*='note-item']")
        count = await cards.count()

        for i in range(count):
            if len(posts_by_url) >= limit:
                break
            card = cards.nth(i)
            try:
                link = card.locator("a[href*='/explore/'], a[href*='/discovery/item/']").first
                href = await link.get_attribute("href", timeout=1000)
                if not href:
                    continue
                if href.startswith("/"):
                    href = BASE_URL + href
                if "/explore/" not in href and "/discovery/item/" not in href:
                    continue
                dedupe_url = href.split("?")[0]
                if dedupe_url in posts_by_url:
                    continue

                title = await first_text(card, [".title", ".note-title", "[class*='title']", "a"])
                preview_text = ""
                try:
                    preview_text = (await card.inner_text(timeout=1000)).strip()
                except Exception:
                    preview_text = title
                if not keyword_matches(f"{title}\n{preview_text}", keyword):
                    filtered_count += 1
                    if filtered_count <= 5:
                        print(f"  [过滤] 卡片不含关键词，跳过：{title or preview_text[:30]}", flush=True)
                    continue
                author = await first_text(card, [".author", ".name", ".user-name"])
                like_text = await first_text(card, [".like-wrapper", ".count", ".likes", "span:has-text('赞')"])
                note_type = "video" if await card.locator("svg, .play-icon, .video-icon, [class*='play']").count() else ""

                posts_by_url[dedupe_url] = Post(
                    rank=len(posts_by_url) + 1,
                    note_id=note_id_from_url(dedupe_url),
                    url=href,
                    title=title,
                    preview_text=preview_text,
                    author=author,
                    like_count=parse_count(like_text),
                    note_type=note_type,
                )
                print(f"  [命中] {len(posts_by_url)}/{limit} {title} | {href}", flush=True)
            except Exception:
                continue

        current_count = len(posts_by_url)
        stable_rounds = stable_rounds + 1 if current_count == previous_count else 0
        previous_count = current_count
        if current_count >= limit:
            break
        await page.mouse.wheel(0, 1800)
        await human_pause(1.2, 2.4)

    posts = list(posts_by_url.values())[:limit]
    print(f"[搜索] 命中 {len(posts)} 个包含关键词的帖子，过滤无关卡片 {filtered_count} 个", flush=True)
    return posts


async def open_post_from_search(page: Page, post: Post) -> None:
    target = post.url.split("?")[0]
    cards = page.locator("section.note-item, div.note-item, [class*='note-item']")
    count = await cards.count()
    for index in range(count):
        card = cards.nth(index)
        try:
            link = card.locator("a[href*='/explore/'], a[href*='/discovery/item/']").first
            href = await link.get_attribute("href", timeout=1000)
            if not href:
                continue
            if href.startswith("/"):
                href = BASE_URL + href
            if href.split("?")[0] != target:
                continue
            await card.click(timeout=5000)
            await wait_for_optional_network_idle(page, timeout=8000)
            await human_pause(1.2, 2.2)
            post.url = page.url
            return
        except Exception:
            continue
    raise RuntimeError(f"搜索页中找不到帖子卡片：{post.title or target}")


async def extract_comment_from_item(item: Locator) -> Comment | None:
    raw = ""
    try:
        raw = (await item.inner_text(timeout=1200)).strip()
    except Exception:
        return None
    if not raw or len(raw) < 2:
        return None

    author = await first_text(item, [".author", ".name", ".user-name", "a"])
    content = await first_text(item, [".content", ".comment-content", ".note-text", "p", "span"])
    like_text = await first_text(item, [".like", ".like-count", ".count", "[class*='like']"])
    time_text = await first_text(item, [".date", ".time", ".reply-time", "[class*='time']"])
    location = await first_text(item, [".location", "[class*='location']"])

    if not content:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        content = lines[1] if len(lines) > 1 else lines[0]

    comment_id = ""
    try:
        comment_id = await item.get_attribute("id") or await item.get_attribute("data-id") or ""
    except Exception:
        pass
    if not comment_id:
        comment_id = str(abs(hash(raw)))

    return Comment(
        comment_id=comment_id,
        author=author,
        content=content,
        like_count=parse_count(like_text),
        time_text=time_text,
        location=location,
        raw_text=raw,
    )


async def extract_replies_from_item(item: Locator, max_replies: int) -> list[Comment]:
    if max_replies <= 0:
        return []
    replies: dict[str, Comment] = {}
    reply_selectors = [
        ".reply-item",
        ".reply-container .comment-item",
        "[class*='reply-item']",
        "[class*='reply'] [class*='comment-item']",
        "[class*='reply'] [class*='commentItem']",
    ]
    for selector in reply_selectors:
        try:
            nodes = item.locator(selector)
            count = await nodes.count()
            for index in range(min(count, max_replies)):
                reply = await extract_comment_from_item(nodes.nth(index))
                if reply and reply.content and reply.comment_id not in replies:
                    replies[reply.comment_id] = reply
                if len(replies) >= max_replies:
                    return list(replies.values())
        except Exception:
            continue
    return list(replies.values())


async def find_comment_container(page: Page) -> Locator:
    selectors = [
        ".comments-el",
        ".comments-container",
        ".comment-list",
        "[class*='comment'][class*='list']",
        "body",
    ]
    for selector in selectors:
        loc = page.locator(selector).first
        try:
            if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                return loc
        except Exception:
            continue
    return page.locator("body")


async def scrape_comments(page: Page, hot_comment_count: int, max_replies_per_comment: int, max_idle_rounds: int = 3) -> list[Comment]:
    container = await find_comment_container(page)
    comments: dict[str, Comment] = {}
    previous_count = -1
    idle_rounds = 0
    scan_rounds = 0
    max_scan_rounds = max(3, min(6, hot_comment_count + 2))
    target_root_comments = max(hot_comment_count, 1)

    while idle_rounds < max_idle_rounds and scan_rounds < max_scan_rounds:
        scan_rounds += 1
        items = page.locator(".parent-comment, [class*='parent-comment']")
        if await items.count() == 0:
            items = page.locator(
                ".comment-item, .comment-inner-container, [class*='comment-item'], [class*='commentItem']"
            )
        count = await items.count()
        for i in range(min(count, target_root_comments + 8)):
            item = items.nth(i)
            comment = await extract_comment_from_item(item)
            if comment and comment.content and comment.comment_id not in comments:
                comment.replies = await extract_replies_from_item(item, max_replies_per_comment)
                comments[comment.comment_id] = comment
            if len(comments) >= target_root_comments and idle_rounds >= 1:
                break

        current_count = len(comments)
        idle_rounds = idle_rounds + 1 if current_count == previous_count else 0
        previous_count = current_count
        print(f"  [评论] 扫描第 {scan_rounds} 轮，主评论 {current_count}/{target_root_comments}", flush=True)

        if current_count >= target_root_comments and idle_rounds >= 1:
            break

        # 少量展开与目标相关的评论/回复，避免误点页面里的大量“查看更多”触发风控。
        clicked_expand = 0
        for text in ["更多回复", "查看回复", "展开回复"]:
            try:
                buttons = page.locator(f"text={text}")
                for idx in range(min(await buttons.count(), 3)):
                    btn = buttons.nth(idx)
                    if await btn.is_visible(timeout=500):
                        await btn.click(timeout=1000)
                        clicked_expand += 1
                        await human_pause(0.3, 0.8)
                    if clicked_expand >= 3:
                        break
            except Exception:
                pass
            if clicked_expand >= 3:
                break

        try:
            await container.evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
        except Exception:
            await page.mouse.wheel(0, 900)
        await human_pause(1.2, 2.0)

    hot_comments = sorted(comments.values(), key=lambda c: c.like_count, reverse=True)[:hot_comment_count]
    for comment in hot_comments:
        if len(comment.replies) < max_replies_per_comment:
            # 展开按钮可能在后续滚动后才出现，再扫一次同 id 的节点以补足回复。
            try:
                nodes = page.locator(
                    ".parent-comment, [class*='parent-comment'], .comment-item, [class*='comment-item']"
                )
                for index in range(await nodes.count()):
                    candidate = await extract_comment_from_item(nodes.nth(index))
                    if candidate and candidate.comment_id == comment.comment_id:
                        comment.replies = await extract_replies_from_item(nodes.nth(index), max_replies_per_comment)
                        break
            except Exception:
                pass
    return hot_comments


async def scrape_open_post(page: Page, post: Post, keyword: str, hot_comment_count: int, max_replies_per_comment: int) -> Post:
    main = page.locator(
        ".note-content, .note-detail, [class*='note-content'], [class*='note-detail'], [class*='interaction-container']"
    ).first
    root = main if await main.count() > 0 else page.locator("body")
    title = await first_text(root, [
        "#detail-title",
        "[class*='title']",
        ".note-title",
        "h1",
    ])
    note_text = await combined_text(root, [
        "#detail-title",
        ".desc",
        "[class*='desc']",
        "[class*='content']",
        ".note-text",
    ])
    author = await first_text(root, [".author .name", ".username", ".user-name", "[class*='user'] [class*='name']"])
    comment_count = await first_text(page.locator("body"), [".comments-title", "[class*='comment-count']"])
    if title and keyword_matches(f"{title}\n{note_text}", keyword):
        post.title = title
    if author:
        post.author = author
    post.comment_count_text = comment_count
    detail_text = f"{title}\n{note_text}"
    if not keyword_matches(detail_text, keyword):
        raise RuntimeError(f"详情页内容不包含关键词：{keyword}")

    comments = await scrape_comments(page, hot_comment_count, max_replies_per_comment)
    reply_count = sum(len(comment.replies) for comment in comments)
    post.comments = comments
    post.comments_count = len(comments)
    post.reply_count = reply_count
    post.total_count_crawled = len(comments) + reply_count
    post.top_10_comments = sorted(comments, key=lambda c: c.like_count, reverse=True)[:10]
    post.top_comments = sorted(comments, key=lambda c: c.like_count, reverse=True)[:hot_comment_count]
    return post


def save_output(keyword: str, output: Path, posts: list[Post], hot_comment_count: int, max_replies_per_comment: int, content_type: str, sort: str) -> None:
    data: dict[str, Any] = {
        "platform": "xhs",
        "keyword": keyword,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_posts": len(posts),
        "comment_mode": "hot",
        "hot_comments_per_post": hot_comment_count,
        "max_replies_per_comment": max_replies_per_comment,
        "filters": {
            "content_type": content_type,
            "sort": sort,
        },
        "posts": [asdict(p) for p in posts],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def run(keyword: str, limit: int, output: Path, profile_dir: Path, headless: bool, hot_comment_count: int, max_replies_per_comment: int, content_type: str, sort: str) -> None:
    cleanup_stale_chrome_locks(profile_dir)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            viewport={"width": 1440, "height": 1000},
            locale="zh-CN",
        )
        try:
            page = await context.new_page()
            await open_search(page, keyword, content_type, sort)
            posts = await collect_posts(page, limit, keyword)
            if not posts:
                raise RuntimeError(
                    "没有抓到帖子。可能是未登录、搜索页被安全验证限制，或当前筛选条件下没有结果。"
                    "请先在可视化浏览器里确认页面能正常显示搜索结果。"
                )

            scraped: list[Post] = []
            for index, post in enumerate(posts, start=1):
                print(f"[{index}/{len(posts)}] 抓取：{post.title} | {post.url}")
                try:
                    await open_post_from_search(page, post)
                    scraped.append(await scrape_open_post(page, post, keyword, hot_comment_count, max_replies_per_comment))
                except Exception as exc:
                    post.scrape_error = f"{type(exc).__name__}: {exc}"
                    print(f"  跳过该帖子，原因：{post.scrape_error}")
                if scraped:
                    save_output(keyword, output, scraped, hot_comment_count, max_replies_per_comment, content_type, sort)
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=10000)
                    await wait_for_optional_network_idle(page, timeout=5000)
                    await human_pause(0.8, 1.5)
                except Exception:
                    await open_search(page, keyword, content_type, sort)
                await human_pause(2.0, 4.0)

            if not scraped:
                raise RuntimeError("帖子详情均未成功抓取，未生成有效评论结果。")
            save_output(keyword, output, scraped, hot_comment_count, max_replies_per_comment, content_type, sort)
            print(f"已保存：{output.resolve()}")
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="抓取小红书关键词搜索结果前 N 个帖子评论并保存为 JSON。")
    parser.add_argument("--keyword", default="首尔病", help="搜索关键词")
    parser.add_argument("--limit", type=int, default=10, help="抓取帖子数量")
    parser.add_argument("--output", type=Path, default=Path("xhs_comments.json"), help="输出 JSON 文件")
    parser.add_argument("--profile-dir", type=Path, default=DEFAULT_PROFILE_DIR, help="浏览器登录态目录")
    parser.add_argument("--hot-comment-count", type=int, default=20, help="每个帖子抓取的热门主评论数量")
    parser.add_argument("--max-replies-per-comment", type=int, default=200, help="每条热门评论最多抓取多少条回复")
    parser.add_argument("--content-type", default="all", choices=["all", "image", "video"], help="搜索结果内容类型")
    parser.add_argument(
        "--sort",
        default="general",
        choices=["general", "latest", "most_liked", "most_commented", "most_collected", "hot"],
        help="搜索结果排序：general 综合，latest 最新，most_liked 最多点赞，most_commented 最多评论，most_collected 最多收藏",
    )
    parser.add_argument("--login", action="store_true", help="打开浏览器手动登录并保存登录态")
    parser.add_argument("--auto-login", action="store_true", help="自动检测登录成功后退出")
    parser.add_argument("--login-timeout", type=int, default=180, help="自动登录检测超时时间")
    parser.add_argument("--headless", action="store_true", help="无头模式运行；调试时建议不要开")
    args = parser.parse_args()

    if args.login:
        asyncio.run(login(args.profile_dir, args.auto_login, args.login_timeout))
    else:
        asyncio.run(run(args.keyword, args.limit, args.output, args.profile_dir, args.headless, args.hot_comment_count, args.max_replies_per_comment, args.content_type, args.sort))


if __name__ == "__main__":
    main()
