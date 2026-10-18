
import os
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = Path("seen_news.json")
MAX_ITEMS = 12

QUERIES = [
    ("加密货币", "Bitcoin OR BTC OR Ethereum OR crypto regulation"),
    ("加密项目", "crypto project funding OR blockchain exploit OR DeFi"),
    ("美股", "US stocks OR S&P 500 OR Nasdaq earnings"),
    ("宏观经济", "Federal Reserve OR US inflation OR Treasury yields"),
    ("AI科技", "artificial intelligence OR AI chips OR data center"),
    ("中文财经", "比特币 OR 美股 OR 美联储 OR 通胀"),
]

def fetch(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 news-watch-bot/1.0"}
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read()

def get_items(category, query):
    url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({
            "q": query,
            "hl": "zh-CN",
            "gl": "US",
            "ceid": "US:zh-Hans"
        })
    )
    root = ET.fromstring(fetch(url))
    items = []

    for item in root.findall("./channel/item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        source = item.findtext("source", "").strip()

        if not title or not link:
            continue

        try:
            dt = parsedate_to_datetime(pub_date)
            from zoneinfo import ZoneInfo
published = dt.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M 北京时间")
        except Exception:
            published = pub_date or "时间未知"

        items.append({
            "key": link,
            "category": category,
            "title": title,
            "link": link,
            "source": source or "Google News",
            "published": published,
        })

    return items

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=25) as response:
        result = json.loads(response.read().decode())
        if not result.get("ok"):
            raise RuntimeError("Telegram API returned ok=false")

def main():
    try:
        known = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else set()
    except Exception:
        known = set()

    found = []
    errors = []

    for category, query in QUERIES:
        try:
            found.extend(get_items(category, query))
        except Exception as exc:
            errors.append(f"{category}: {type(exc).__name__}")

    # Keep only new stories, preserving feed order.
    fresh = []
    local_seen = set()
    for item in found:
        if item["key"] not in known and item["key"] not in local_seen:
            fresh.append(item)
            local_seen.add(item["key"])

    # Limit notifications to avoid flooding Telegram.
    batch = fresh[:MAX_ITEMS]

    if batch:
        lines = ["📰 新闻监控", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), ""]
        for i, item in enumerate(batch, 1):
            lines.extend([
                f"{i}. [{item['category']}] {item['title']}",
                f"来源：{item['source']}｜时间：{item['published']}",
                item["link"],
                ""
            ])
        message = "\n".join(lines)
        # Telegram messages have a length limit.
        send_telegram(message[:3900])

        # Mark as seen only after successful delivery.
        known.update(item["key"] for item in batch)

    # Remember the newest feed entries too, so the first run won't replay
    # the entire backlog on the next run.
    known.update(item["key"] for item in found)
    STATE_FILE.write_text(
        json.dumps(list(known)[-1000:], ensure_ascii=False, indent=2)
    )

    print(f"Fetched {len(found)} items; new {len(fresh)}; sent {len(batch)}")
    if errors:
        print("Feed errors:", "; ".join(errors))

if __name__ == "__main__":
    main()
