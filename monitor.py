
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from pathlib import Path
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo


# ==================================================
# 1. 基础配置
# ==================================================

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
STATE_FILE = Path("seen_news.json")

# 严格限制：只允许最近 60 分钟发布的新闻
MAX_AGE_MINUTES = 60

# 容忍最多 5 分钟的未来时间误差
FUTURE_TOLERANCE_MINUTES = 5

# 每次最多推送条数
MAX_ITEMS = 10

# 去重记录最多保存 2000 条
MAX_HISTORY = 2000

# ==================================================
# 2. 新闻搜索关键词
# 所有 Google News 查询都限制在最近 1 小时
# ==================================================

QUERIES = [
    ("加密货币", "Bitcoin OR BTC OR Ethereum OR crypto"),
    ("DeFi与安全", "DeFi OR crypto exploit OR blockchain hack"),
    ("交易所动态", "crypto exchange OR Bitcoin ETF OR stablecoin"),
    ("美股", "US stocks OR Nasdaq OR S&P 500 earnings"),
    ("宏观经济", "Federal Reserve OR US inflation OR Treasury yields"),
    ("AI科技", "artificial intelligence OR AI chips OR data center"),
    ("中国财经", "美股 OR 比特币 OR 美联储 OR 通胀 OR 加密货币"),
    ("中国科技", "人工智能 OR 半导体 OR 芯片 OR 数据中心"),

    # 中文媒体定向搜索
    ("中新网", "site:chinanews.com.cn 财经 OR 科技 OR 国际"),
    ("36氪", "site:36kr.com AI OR 科技 OR 创投"),
    ("经济观察网", "site:eeo.com.cn 财经 OR 公司 OR 产业"),
    ("财新", "site:caixin.com 财经 OR 宏观 OR 公司"),
    ("澎湃新闻", "site:thepaper.cn 财经 OR 科技 OR 国际"),
    ("证券时报", "site:stcn.com A股 OR 美股 OR 产业"),
]

# ==================================================
# 3. 中文媒体直接 RSS
# 这些源可能调整地址或暂时不可用；
# 即使抓取成功，也必须通过发布时间过滤。
# ==================================================

RSS_SOURCES = [
    (
        "中新网·即时新闻",
        "https://www.chinanews.com.cn/rss/scroll-news.xml"
    ),
    (
        "中新网·财经",
        "https://www.chinanews.com.cn/rss/finance.xml"
    ),
    (
        "中新网·国际",
        "https://www.chinanews.com.cn/rss/world.xml"
    ),
    (
        "中新网·证券",
        "https://www.chinanews.com.cn/rss/stock.xml"
    ),
    (
        "36氪",
        "https://36kr.com/feed"
    ),
    (
        "经济观察网",
        "https://app.eeo.com.cn/rss.php"
    ),
]


# ==================================================
# 4. 网络请求
# ==================================================

def fetch(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 news-watch-bot/2.0",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read()


# ==================================================
# 5. 时间处理
# 没有有效发布时间的新闻一律不推送
# ==================================================

def parse_date(value):
    if not value:
        return None

    try:
        dt = parsedate_to_datetime(value.strip())

        if dt is None:
            return None

        # RSS 时间通常自带时区。
        # 如果缺失时区，无法可靠判断新旧，直接丢弃。
        if dt.tzinfo is None:
            return None

        return dt.astimezone(timezone.utc)

    except (TypeError, ValueError, OverflowError):
        return None


def is_fresh(published_dt, now):
    if published_dt is None:
        return False

    age = now - published_dt

    if age < timedelta(minutes=-FUTURE_TOLERANCE_MINUTES):
        return False

    if age > timedelta(minutes=MAX_AGE_MINUTES):
        return False

    return True


def format_beijing_time(published_dt):
    return published_dt.astimezone(BEIJING_TZ).strftime(
        "%Y-%m-%d %H:%M"
    )


def current_beijing_time():
    return datetime.now(BEIJING_TZ).strftime(
        "%Y-%m-%d %H:%M 北京时间"
    )


# ==================================================
# 6. Google News RSS
# 查询条件同时限制最近 1 小时
# ==================================================

def google_news_url(query):
    params = {
        "q": f"({query}) when:1h",
        "hl": "zh-CN",
        "gl": "CN",
        "ceid": "CN:zh-Hans",
    }

    return (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode(params)
    )


# ==================================================
# 7. 解析 RSS / Atom
# ==================================================

def get_items(category, query=None, feed_url=None, now=None):
    if now is None:
        now = datetime.now(timezone.utc)

    url = feed_url if feed_url else google_news_url(query)

    root = ET.fromstring(fetch(url))
    entries = root.findall("./channel/item")

    atom_ns = "{http://www.w3.org/2005/Atom}"

    if not entries:
        entries = root.findall(f".//{atom_ns}entry")

    items = []

    for entry in entries:
        is_atom = entry.tag.endswith("entry")

        if is_atom:
            title = entry.findtext(
                f"{atom_ns}title", ""
            ).strip()

            link_element = entry.find(f"{atom_ns}link")

            link = ""
            if link_element is not None:
                link = (
                    link_element.attrib.get("href", "")
                    or link_element.text
                    or ""
                ).strip()

            pub_date = (
                entry.findtext(f"{atom_ns}published", "")
                or entry.findtext(f"{atom_ns}updated", "")
            )

            source_element = entry.find(f"{atom_ns}source")
            source = ""

            if source_element is not None:
                source = source_element.findtext(
                    f"{atom_ns}title", ""
                ).strip()

        else:
            title = entry.findtext("title", "").strip()
            link = entry.findtext("link", "").strip()

            pub_date = (
                entry.findtext("pubDate", "")
                or entry.findtext("published", "")
                or entry.findtext("updated", "")
            )

            source_element = entry.find("source")
            source = (
                source_element.text.strip()
                if source_element is not None and source_element.text
                else ""
            )

        if not title or not link:
            continue

        published_dt = parse_date(pub_date)

        # 核心规则：旧新闻、无日期新闻、异常未来时间全部丢弃
        if not is_fresh(published_dt, now):
            continue

        items.append({
            "key": link,
            "category": category,
            "title": title,
            "link": link,
            "source": source or category,
            "published_dt": published_dt,
            "published": format_beijing_time(published_dt),
        })

    return items


# ==================================================
# 8. Telegram 推送
# ==================================================

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram 推送失败：" + str(result.get("description", "未知错误"))
        )


# ==================================================
# 9. 去重记录
# 只记录已成功推送的新闻
# ==================================================

def load_history():
    if not STATE_FILE.exists():
        return []

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))

        if isinstance(data, list):
            return [str(item) for item in data]

    except (OSError, json.JSONDecodeError):
        print("警告：去重文件读取失败，将重新建立记录。")

    return []


def save_history(history):
    # 保留顺序，并限制文件大小
    history = list(dict.fromkeys(history))[-MAX_HISTORY:]

    STATE_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ==================================================
# 10. 主程序
# ==================================================

def main():
    now = datetime.now(timezone.utc)
    history = load_history()
    known = set(history)

    found = []
    errors = []

    # 抓取关键词搜索结果
    for category, query in QUERIES:
        try:
            items = get_items(
                category=category,
                query=query,
                now=now,
            )
            found.extend(items)

            print(f"[成功] {category}: {len(items)} 条新鲜新闻")

        except Exception as exc:
            errors.append(
                f"{category}: {type(exc).__name__}: {exc}"
            )
            print(f"[失败] {category}: {type(exc).__name__}: {exc}")

    # 抓取直接订阅的中文媒体
    for category, feed_url in RSS_SOURCES:
        try:
            items = get_items(
                category=category,
                feed_url=feed_url,
                now=now,
            )
            found.extend(items)

            print(f"[成功] {category}: {len(items)} 条新鲜新闻")

        except Exception as exc:
            errors.append(
                f"{category}: {type(exc).__name__}: {exc}"
            )
            print(f"[失败] {category}: {type(exc).__name__}: {exc}")

    # 按链接去重
    unique = []
    local_seen = set()

    for item in found:
        key = item["key"]

        if key in local_seen:
            continue

        local_seen.add(key)
        unique.append(item)

    # 按发布时间从新到旧排列
    unique.sort(
        key=lambda item: item["published_dt"],
        reverse=True,
    )

    # 只推送未发送过的新闻
    fresh = [
        item for item in unique
        if item["key"] not in known
    ]

    batch = fresh[:MAX_ITEMS]

    if batch:
        lines = [
            "📰 最新新闻监控",
            current_beijing_time(),
            f"有效时间范围：最近 {MAX_AGE_MINUTES} 分钟",
            "",
        ]

        for index, item in enumerate(batch, 1):
            lines.extend([
                f"{index}. [{item['category']}] {item['title'][:180]}",
                f"发布时间：{item['published']}｜来源：{item['source']}",
                item["link"],
                "",
            ])

        # 控制在 Telegram 消息长度限制以内
        message = "\n".join(lines)

        if len(message) > 3900:
            lines = [
                "📰 最新新闻监控",
                current_beijing_time(),
                f"有效时间范围：最近 {MAX_AGE_MINUTES} 分钟",
                "",
            ]

            for index, item in enumerate(batch, 1):
                lines.extend([
                    f"{index}. [{item['category']}] {item['title'][:100]}",
                    f"发布时间：{item['published']}",
                    item["link"][:300],
                    "",
                ])

            message = "\n".join(lines)[:3900]

        # 发送成功后再写入去重记录
        send_telegram(message)

        for item in batch:
            if item["key"] not in known:
                history.append(item["key"])
                known.add(item["key"])

        save_history(history)

        print(f"成功推送 {len(batch)} 条新闻")

    else:
        print("本轮没有符合条件的未读新闻，不发送旧新闻。")

    print(f"抓取记录总数：{len(found)}")
    print(f"去重后数量：{len(unique)}")
    print(f"符合时间条件且未推送：{len(fresh)}")
    print(f"本轮推送：{len(batch)}")

    if errors:
        print(f"有 {len(errors)} 个来源抓取失败：")
        for error in errors:
            print("-", error)


if __name__ == "__main__":
    main()
