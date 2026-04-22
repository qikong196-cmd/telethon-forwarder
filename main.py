from telethon import TelegramClient, events
from telethon.sessions import StringSession
import hashlib
import json
import os
import asyncio
import random
import re
import logging
import time
from pathlib import Path
from collections import deque

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION_STRING = os.getenv("TG_SESSION_STRING", "")
TARGET_CHAT = os.getenv("TARGET_CHAT", "ai_r444")
DEDUP_FILE = os.getenv("DEDUP_FILE", "/tmp/sent_hashes.json")
MAX_TEXT_LEN = int(os.getenv("MAX_TEXT_LEN", "420"))
DEDUP_WINDOW = int(os.getenv("DEDUP_WINDOW", "1500"))
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))
DUPLICATE_HOURS = float(os.getenv("DUPLICATE_HOURS", "8"))

SOURCE_CHATS_RAW = os.getenv(
    "SOURCE_CHATS",
    "bx666,bx666bbb,miandianDS,dny858,jpzhadsj,bg123,ft5868a",
)

SOURCE_PRIORITY_RAW = os.getenv("SOURCE_PRIORITY", "")

HEADER = os.getenv("POST_HEADER", "")
FOOTER = os.getenv(
    "POST_FOOTER",
    "👉 海外交友群：https://t.me/ai_r4444\n"
    "✈️ 投稿爆料澄清：@rr_44i\n"
    "👉 关注那些事 》@ai_r444",
)

SOURCE_CHATS = [x.strip().lstrip("@").lower() for x in SOURCE_CHATS_RAW.split(",") if x.strip()]

if not API_ID or not API_HASH:
    raise ValueError("缺少 TG_API_ID 或 TG_API_HASH 环境变量")

if not SESSION_STRING:
    raise ValueError("缺少 TG_SESSION_STRING 环境变量")

if not SOURCE_CHATS:
    raise ValueError("SOURCE_CHATS 为空，请至少填写一个来源频道/群组")


def parse_source_priority(raw: str) -> dict:
    result = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        name, score = item.split(":", 1)
        name = name.strip().lstrip("@").lower()
        try:
            result[name] = int(score.strip())
        except ValueError:
            continue
    return result


SOURCE_PRIORITY = parse_source_priority(SOURCE_PRIORITY_RAW)
DUPLICATE_SECONDS = int(DUPLICATE_HOURS * 3600)


def ensure_parent_dir(file_path: str):
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)


def load_state():
    path = Path(DEDUP_FILE)
    default_state = {
        "strict_hashes": [],
        "loose_hashes": [],
        "media_hashes": [],
        "recent_items": [],
    }

    if not path.exists():
        return default_state

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {
                    "strict_hashes": data.get("strict_hashes", []),
                    "loose_hashes": data.get("loose_hashes", []),
                    "media_hashes": data.get("media_hashes", []),
                    "recent_items": data.get("recent_items", []),
                }
    except Exception as e:
        logging.warning("读取去重状态失败: %s", e)

    return default_state


def save_state():
    try:
        ensure_parent_dir(DEDUP_FILE)
        data = {
            "strict_hashes": list(strict_hashes),
            "loose_hashes": list(loose_hashes),
            "media_hashes": list(media_hashes),
            "recent_items": list(recent_items),
        }
        with open(DEDUP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning("保存去重状态失败: %s", e)


state = load_state()
strict_hashes = deque(state["strict_hashes"][-DEDUP_WINDOW:], maxlen=DEDUP_WINDOW)
loose_hashes = deque(state["loose_hashes"][-DEDUP_WINDOW:], maxlen=DEDUP_WINDOW)
media_hashes = deque(state["media_hashes"][-DEDUP_WINDOW:], maxlen=DEDUP_WINDOW)
recent_items = deque(state["recent_items"][-DEDUP_WINDOW:], maxlen=DEDUP_WINDOW)


def get_source_patterns():
    patterns = []
    for name in SOURCE_CHATS:
        escaped = re.escape(name)
        patterns.extend([
            rf"https?://t\.me/{escaped}(?:/\S*)?",
            rf"t\.me/{escaped}(?:/\S*)?",
            rf"telegram\.me/{escaped}(?:/\S*)?",
            rf"@{escaped}\b",
        ])
    return patterns


SOURCE_PATTERNS = get_source_patterns()


def get_source_username(event) -> str:
    try:
        chat = event.chat
        username = getattr(chat, "username", None)
        if username:
            return username.lower()
    except Exception:
        pass
    return ""


def get_source_priority(source_username: str) -> int:
    if not source_username:
        return 0
    return SOURCE_PRIORITY.get(source_username.lower(), 0)


def get_chat_name(event) -> str:
    try:
        chat = event.chat
        if getattr(chat, "username", None):
            return f"@{chat.username}"
        if getattr(chat, "title", None):
            return chat.title
        if getattr(chat, "first_name", None):
            return chat.first_name
    except Exception:
        pass
    return "unknown_chat"


def remove_source_links(text: str) -> str:
    text = text or ""
    for pattern in SOURCE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text


def clean_text(text: str) -> str:
    text = (text or "").strip()
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    text = remove_source_links(text)

    text = re.sub(r"https?://t\.me/\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"t\.me/\S+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE)

    remove_line_keywords = [
        "人民日报",
        "人民日报曝",
        "人民日报爆",
        "东南亚大事件",
        "消息汇总",
        "东南亚那些事",
        "订阅柬埔寨黑暗事件",
        "柬埔寨黑暗事件",
        "欢迎投稿",
        "投稿",
        "同城交友",
        "交友",
        "更多情况持续关注",
        "评论区聊聊",
        "你怎么看",
        "欢迎留言",
        "欢迎讨论",
        "海外交友群",
        "投稿爆料澄清",
        "关注那些事",
    ]
    for kw in remove_line_keywords:
        text = re.sub(rf"(?im)^.*{re.escape(kw)}.*$", "", text)

    text = re.sub(r"#\w+\b", "", text)
    text = re.sub(r"(?<!\w)@\w+", "", text)

    text = re.sub(r"[⚡🦑🔠👈👉❤️✈️👑📢💥🔥✅✔️☑️😭📣🔊👇🔗🧪➡️]", "", text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def trim_text(text: str) -> str:
    if len(text) > MAX_TEXT_LEN:
        return text[:MAX_TEXT_LEN] + "..."
    return text


def normalize_strict(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"^【.*?】", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fa5]", "", text)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def normalize_loose(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r"^【.*?】", "", text)
    text = re.sub(
        r"(警方通报|最新情况|刚刚通报|突发|现场曝光|最新|关注|东南亚快讯|东南亚那些事|快讯|消息汇总|人民日报|东南亚大事件)",
        "",
        text,
    )
    text = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def make_strict_hash(text: str, media_count: int = 0) -> str:
    core = normalize_strict(text)
    raw = f"{core[:180]}|len={len(core)}|media={media_count}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def make_loose_hash(text: str) -> str:
    core = normalize_loose(text)
    words = core.split()
    if len(words) > 80:
        words = words[:80]
    raw = " ".join(words)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_media_signature(media) -> str:
    if not media:
        return ""

    try:
        if hasattr(media, "photo") and media.photo:
            p = media.photo
            return f"photo:{getattr(p, 'id', '')}:{getattr(p, 'access_hash', '')}"

        if hasattr(media, "document") and media.document:
            d = media.document
            return (
                f"doc:{getattr(d, 'id', '')}:{getattr(d, 'access_hash', '')}:"
                f"{getattr(d, 'size', '')}:{getattr(d, 'mime_type', '')}"
            )
    except Exception:
        return ""

    return ""


def make_album_media_hash(messages) -> str:
    signatures = []
    for m in messages or []:
        if getattr(m, "media", None):
            sig = get_media_signature(m.media)
            if sig:
                signatures.append(sig)

    if not signatures:
        return ""

    raw = "|".join(sorted(signatures))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def make_single_media_hash(message) -> str:
    if not getattr(message, "media", None):
        return ""
    sig = get_media_signature(message.media)
    if not sig:
        return ""
    return hashlib.md5(sig.encode("utf-8")).hexdigest()


def text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0

    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a or not set_b:
        return 0.0

    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    if union == 0:
        return 0.0

    return inter / union


def is_duplicate(text: str, source_username: str, media_hash: str = "", media_count: int = 0) -> tuple[bool, str]:
    now_ts = int(time.time())
    strict_h = make_strict_hash(text, media_count=media_count)
    loose_h = make_loose_hash(text)
    loose_text = normalize_loose(text)
    current_priority = get_source_priority(source_username)

    if media_hash and media_hash in media_hashes:
        return True, "命中媒体去重"

    if strict_h in strict_hashes:
        return True, "命中严格去重"

    if loose_h in loose_hashes:
        return True, "命中宽松去重"

    if loose_text:
        recent_list = list(recent_items)[-250:]
        for item in recent_list:
            old_text = item.get("text", "")
            old_ts = int(item.get("ts", 0))
            old_source = item.get("source", "")
            old_priority = int(item.get("priority", 0))
            old_media_hash = item.get("media_hash", "")

            if now_ts - old_ts > DUPLICATE_SECONDS:
                continue

            if media_hash and old_media_hash and media_hash == old_media_hash:
                return True, f"命中历史媒体相同去重: {old_source}"

            score = text_similarity(loose_text, old_text)
            if score >= SIMILARITY_THRESHOLD:
                if current_priority < old_priority:
                    return True, f"命中高优先来源去重: {old_source} | 相似度={score:.2f}"
                return True, f"命中时间窗相似去重: {score:.2f}"

    return False, ""


def remember_post(text: str, source_username: str, media_hash: str = "", media_count: int = 0):
    strict_hashes.append(make_strict_hash(text, media_count=media_count))
    loose_hashes.append(make_loose_hash(text))

    if media_hash:
        media_hashes.append(media_hash)

    loose_text = normalize_loose(text)
    if loose_text or media_hash:
        recent_items.append({
            "text": loose_text,
            "ts": int(time.time()),
            "source": source_username,
            "priority": get_source_priority(source_username),
            "media_hash": media_hash,
        })

    save_state()


LOCATION_RULES = [
    ("#东南亚", ["东南亚", "南洋"]),
    ("#缅甸", ["缅甸", "掸邦", "克钦", "内比都"]),
    ("#果敢", ["果敢"]),
    ("#仰光", ["仰光"]),
    ("#曼德勒", ["曼德勒"]),
    ("#妙瓦底", ["妙瓦底", "苗瓦迪", "myawaddy"]),
    ("#大其力", ["大其力", "tachileik"]),
    ("#佤邦", ["佤邦"]),
    ("#柬埔寨", ["柬埔寨"]),
    ("#金边", ["金边"]),
    ("#西港", ["西港", "西哈努克", "sihanoukville"]),
    ("#波贝", ["波贝", "poipet"]),
    ("#木牌", ["木牌", "巴域", "bavet"]),
    ("#泰国", ["泰国"]),
    ("#曼谷", ["曼谷"]),
    ("#芭提雅", ["芭提雅"]),
    ("#清迈", ["清迈"]),
    ("#清莱", ["清莱"]),
    ("#普吉", ["普吉"]),
    ("#老挝", ["老挝"]),
    ("#万象", ["万象"]),
    ("#金三角", ["金三角"]),
    ("#磨丁", ["磨丁"]),
    ("#越南", ["越南"]),
    ("#河内", ["河内"]),
    ("#胡志明", ["胡志明"]),
    ("#岘港", ["岘港"]),
    ("#海防", ["海防"]),
    ("#马来西亚", ["马来西亚"]),
    ("#吉隆坡", ["吉隆坡"]),
    ("#槟城", ["槟城"]),
    ("#新山", ["新山", "柔佛"]),
    ("#菲律宾", ["菲律宾"]),
    ("#马尼拉", ["马尼拉"]),
    ("#宿务", ["宿务"]),
    ("#克拉克", ["克拉克"]),
    ("#帕赛", ["帕赛", "帕賽", "pasay"]),
    ("#新加坡", ["新加坡"]),
    ("#印尼", ["印尼", "印度尼西亚"]),
    ("#雅加达", ["雅加达"]),
    ("#巴淡岛", ["巴淡岛"]),
]

TOPIC_RULES = [
    ("#诈骗", ["诈骗", "电诈", "骗", "骗术", "杀猪盘"]),
    ("#园区", ["园区", "诈骗园", "科技园", "园区内"]),
    ("#警方通报", ["警方", "警察", "公安", "抓捕", "通缉", "逮捕"]),
    ("#遣返", ["遣返", "押解回国", "移交回国"]),
    ("#绑架", ["绑架", "绑走", "掳走", "劫持"]),
    ("#失联", ["失联", "失踪", "联系不上"]),
    ("#解救", ["解救", "营救", "救出"]),
    ("#偷渡", ["偷渡", "非法入境", "非法出境", "蛇头"]),
    ("#枪击", ["枪击", "开枪", "枪战"]),
    ("#冲突", ["冲突", "打架", "斗殴", "火拼"]),
    ("#命案", ["命案", "遇害", "死亡", "被杀", "尸体"]),
    ("#火灾", ["起火", "火灾", "失火"]),
    ("#爆炸", ["爆炸"]),
    ("#赌博", ["赌博", "博彩", "赌场", "赌厅", "盘口"]),
    ("#洗钱", ["洗钱", "跑分", "地下钱庄"]),
    ("#招聘", ["招聘", "招工", "招人", "高薪"]),
    ("#劳工", ["劳工", "工人", "务工", "劳务"]),
    ("#签证", ["签证", "签注", "落地签"]),
    ("#出入境", ["海关", "边检", "口岸", "出入境"]),
    ("#突发", ["突发", "现场", "曝光", "紧急"]),
    ("#新闻", ["消息", "通报", "情况", "事件", "快讯"]),
]


def detect_tags(text: str) -> str:
    text = text or ""
    geo_tags = []
    topic_tags = []

    for tag, keywords in LOCATION_RULES:
        if any(k in text for k in keywords):
            geo_tags.append(tag)

    for tag, keywords in TOPIC_RULES:
        if any(k in text for k in keywords):
            topic_tags.append(tag)

    if "#东南亚" not in geo_tags:
        geo_tags.insert(0, "#东南亚")

    if "#新闻" not in topic_tags:
        topic_tags.append("#新闻")

    combined = geo_tags + topic_tags

    result = []
    seen = set()
    for tag in combined:
        if tag not in seen:
            seen.add(tag)
            result.append(tag)

    return " ".join(result[:8])


def choose_title_pool(text: str):
    if any(x in text for x in ["警方", "公安", "抓捕", "通缉", "遣返"]):
        return ["【警方通报】", "【最新通报】", "【刚刚通报】", "【案件进展】"]
    if any(x in text for x in ["绑架", "失联", "解救"]):
        return ["【最新情况】", "【事件追踪】", "【引发关注】", "【持续关注】"]
    if any(x in text for x in ["枪击", "冲突", "打架", "火拼"]):
        return ["【突发】", "【现场曝光】", "【事发现场】", "【最新画面】"]
    if any(x in text for x in ["起火", "火灾", "爆炸"]):
        return ["【突发】", "【现场画面】", "【最新消息】", "【事故关注】"]
    if any(x in text for x in ["诈骗", "电诈", "园区"]):
        return ["【警惕】", "【曝光】", "【最新】", "【值得关注】"]
    if any(x in text for x in ["签证", "海关", "边检", "出入境"]):
        return ["【提醒】", "【最新消息】", "【出入境关注】", "【政策变化】"]
    return ["【最新】", "【关注】", "【东南亚快讯】", "【最新动态】"]


def get_title(text: str) -> str:
    return random.choice(choose_title_pool(text))


def make_hook(text: str) -> str:
    if any(x in text for x in ["警方", "公安", "抓捕", "通缉", "遣返"]):
        return "👇 最新动态，持续关注"
    if any(x in text for x in ["诈骗", "电诈", "园区"]):
        return "👇 转发提醒身边人，小心踩坑"
    if any(x in text for x in ["冲突", "打架", "现场", "突发", "枪击"]):
        return "👇 现场情况持续发酵"
    if any(x in text for x in ["绑架", "失联", "解救"]):
        return "👇 后续进展值得关注"
    return "👇 更多情况持续关注"


def build_caption(text: str) -> str:
    cleaned = clean_text(text)

    if not cleaned:
        cleaned = "现场画面流出，更多情况持续关注。"

    short_text = trim_text(cleaned)
    tags = detect_tags(cleaned)
    title = get_title(cleaned)
    hook = make_hook(cleaned)

    parts = [title]

    if HEADER and HEADER.strip():
        parts.append(HEADER.strip())

    parts.append(short_text)

    if hook and hook.strip():
        parts.append(hook.strip())

    if tags and tags.strip():
        parts.append(tags.strip())

    if FOOTER and FOOTER.strip():
        parts.append(FOOTER.strip())

    return "\n\n".join(parts)


def is_ad(text: str) -> tuple[bool, str]:
    lower_text = (text or "").lower()

    # 新闻保护（避免误杀）
    if any(x in text for x in ["网友曝光", "警方", "起诉", "诈骗", "案件", "通报"]):
        pass

    # 域名黑名单
    kill_domains = [
        "u8.com",
        "7t.com",
        "9g.com",
        "g7.com",
    ]

    for d in kill_domains:
        if d in lower_text:
            return True, f"命中黑名单域名: {d}"

    # 域名变体
    if re.search(r"u\s*8\s*[\.\。]\s*com", lower_text):
        return True, "命中U8变体"

    if re.search(r"7\s*t\s*[\.\。]\s*com", lower_text):
        return True, "命中7T变体"

    if re.search(r"9\s*g\s*[\.\。]\s*com", lower_text):
        return True, "命中9G变体"

    if re.search(r"g\s*7\s*[\.\。]\s*com", lower_text):
        return True, "命中G7变体"

    # 接单 / 开发类广告
    kill_words = [
        "专注im通讯软件定制搭建",
        "远洋全球达",
    ]

    for w in kill_words:
        if w in lower_text:
            return True, f"命中接单广告: {w}"

    # 明确赌博硬广告
    hard = [
        "送彩金",
        "注册送",
        "邀请码",
        "上分",
        "娱乐城",
        "pg集团",
        "pg直营",
        "官方飞投",
    ]

    for k in hard:
        if k in lower_text:
            return True, f"命中硬广告词: {k}"

    # 赌博词 + 广告语组合，才判广告
    gamble_words = ["百家乐", "盘口", "下注", "出款"]
    ad_words = ["送彩金", "注册", "邀请码", "充值", "平台", "网址"]

    if any(w in lower_text for w in gamble_words):
        if any(x in lower_text for x in ad_words):
            return True, "赌博类广告"

    # 软广告
    soft = [
        "盈利",
        "提款",
        "vip",
        "福利",
        "爆率",
        "资金",
        "贵宾厅",
        "红包",
        "活动",
        "首存",
        "返佣",
        "充值",
        "利润",
    ]

    hit = [k for k in soft if k in lower_text]
    if len(hit) >= 3:
        return True, f"命中软广告词过多: {','.join(hit[:5])}"

    # 赌博表情刷屏
    if "百家乐" in lower_text and ((text or "").count("😀") > 5 or (text or "").count("👍") > 5):
        return True, "表情赌博广告"

    return False, ""


async def delay():
    t = random.randint(5, 12)
    logging.info("延迟 %s 秒后发送", t)
    await asyncio.sleep(t)


def register_handlers(client):
    @client.on(events.Album(chats=SOURCE_CHATS))
    async def album_handler(event):
        try:
            msgs = event.messages or []
            source_name = get_chat_name(event)
            source_username = get_source_username(event)

            raw_text = "\n".join((m.raw_text or "") for m in msgs).strip()
            media_files = [m.media for m in msgs if m.media]
            caption_text = "\n".join((m.raw_text or "") for m in msgs if m.raw_text).strip()
            media_hash = make_album_media_hash(msgs)

            if not media_files:
                logging.info("相册跳过：没有媒体 | 来源=%s", source_name)
                return

            base_text = caption_text or raw_text or "album_only_media"
            cleaned_check_text = clean_text(base_text)

            ad_flag, ad_reason = is_ad(cleaned_check_text)
            if ad_flag:
                logging.info("广告拦截（相册）| 来源=%s | 原因=%s", source_name, ad_reason)
                return

            dup_flag, dup_reason = is_duplicate(
                cleaned_check_text or "album_only_media",
                source_username=source_username,
                media_hash=media_hash,
                media_count=len(media_files),
            )
            if dup_flag:
                logging.info("重复相册，跳过 | 来源=%s | 原因=%s", source_name, dup_reason)
                return

            logging.info(
                "收到相册 | 来源=%s | 优先级=%s | 图片数=%s | 媒体去重=%s | 文本预览=%s",
                source_name,
                get_source_priority(source_username),
                len(media_files),
                "是" if media_hash else "否",
                (cleaned_check_text[:60] if cleaned_check_text else "[无文本]"),
            )

            await delay()
            await client.send_file(
                TARGET_CHAT,
                media_files,
                caption=build_caption(base_text),
                force_document=False,
            )

            remember_post(
                cleaned_check_text or "album_only_media",
                source_username=source_username,
                media_hash=media_hash,
                media_count=len(media_files),
            )
            logging.info("已发相册 | 来源=%s -> 目标=%s", source_name, TARGET_CHAT)

        except Exception as e:
            logging.exception("相册错误: %s", e)

    @client.on(events.NewMessage(chats=SOURCE_CHATS))
    async def message_handler(event):
        try:
            msg = event.message
            source_name = get_chat_name(event)
            source_username = get_source_username(event)

            if msg.grouped_id:
                logging.info("单条跳过：属于相册分组 | 来源=%s", source_name)
                return

            raw_text = msg.raw_text or ""
            has_media = bool(msg.media)
            media_hash = make_single_media_hash(msg)

            logging.info("=== 来源=%s | has_media=%s ===", source_name, has_media)
            logging.info("=== 原始文本=%s ===", raw_text[:500])

            if not raw_text.strip() and has_media:
                raw_text = "现场画面流出，更多情况持续关注。"

            cleaned_text = clean_text(raw_text)
            logging.info("=== 清洗后文本=%s ===", cleaned_text[:500])

            ad_flag, ad_reason = is_ad(cleaned_text)
            logging.info("=== 广告判断=%s | 原因=%s ===", ad_flag, ad_reason)
            if ad_flag:
                logging.info("广告拦截（单条）| 来源=%s | 原因=%s", source_name, ad_reason)
                return

            dup_flag, dup_reason = is_duplicate(
                cleaned_text or "single_only_media",
                source_username=source_username,
                media_hash=media_hash,
                media_count=1 if has_media else 0,
            )
            logging.info("=== 去重判断=%s | 原因=%s ===", dup_flag, dup_reason)
            if dup_flag:
                logging.info("重复消息，跳过 | 来源=%s | 原因=%s", source_name, dup_reason)
                return

            logging.info(
                "收到单条 | 来源=%s | 优先级=%s | 含媒体=%s | 媒体去重=%s | 文本预览=%s",
                source_name,
                get_source_priority(source_username),
                has_media,
                "是" if media_hash else "否",
                (cleaned_text[:60] if cleaned_text else "[无文本]"),
            )

            await delay()

            if has_media:
                await client.send_file(
                    TARGET_CHAT,
                    msg.media,
                    caption=build_caption(cleaned_text),
                )
            else:
                await client.send_message(
                    TARGET_CHAT,
                    build_caption(cleaned_text),
                )

            remember_post(
                cleaned_text or "single_only_media",
                source_username=source_username,
                media_hash=media_hash,
                media_count=1 if has_media else 0,
            )
            logging.info("已发一条 | 来源=%s -> 目标=%s", source_name, TARGET_CHAT)

        except Exception as e:
            logging.exception("单条错误: %s", e)


async def main():
    logging.info("启动中...")
    logging.info("目标频道: %s", TARGET_CHAT)
    logging.info("来源数量: %s", len(SOURCE_CHATS))
    logging.info("来源列表: %s", ", ".join(SOURCE_CHATS))
    logging.info("去重文件: %s", DEDUP_FILE)
    logging.info("相似度阈值: %.2f", SIMILARITY_THRESHOLD)
    logging.info("时间窗(小时): %.2f", DUPLICATE_HOURS)
    logging.info("来源优先级配置: %s", SOURCE_PRIORITY if SOURCE_PRIORITY else "{}")

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    register_handlers(client)

    await client.start()
    logging.info("Telegram 已连接，运行中...")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
