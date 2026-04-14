from telethon import TelegramClient, events
import hashlib
import json
import os
import asyncio
import random
import re
import logging

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION_NAME = os.getenv("TG_SESSION_NAME", "session")
TARGET_CHAT = os.getenv("TARGET_CHAT", "ai_r444")
DEDUP_FILE = os.getenv("DEDUP_FILE", "sent_hashes.json")

SOURCE_CHATS_RAW = os.getenv(
    "SOURCE_CHATS",
    "bx666,bx666bbb,miandianDS,dny858,jpzhadsj,bg123,ft5868a",
)

HEADER = os.getenv("POST_HEADER", "【东南亚那些事】")
FOOTER = os.getenv(
    "POST_FOOTER",
    "👉 海外交友群：https://t.me/ai_r4444\n✈️ 投稿爆料澄清：@rr_44i\n👉 关注那些事 》@ai_r444",
)

SOURCE_CHATS = [x.strip() for x in SOURCE_CHATS_RAW.split(",") if x.strip()]

if not API_ID or not API_HASH:
    raise ValueError("缺少 TG_API_ID 或 TG_API_HASH 环境变量")

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ========= 标签 =========
def detect_tags(text):
    text = text or ""
    tags = []

    if any(x in text for x in ["缅甸", "果敢", "仰光", "大其力"]):
        tags.append("#缅甸")
    if any(x in text for x in ["柬埔寨", "金边", "西港"]):
        tags.append("#柬埔寨")
    if any(x in text for x in ["泰国", "曼谷"]):
        tags.append("#泰国")
    if any(x in text for x in ["老挝"]):
        tags.append("#老挝")
    if any(x in text for x in ["越南", "河内", "胡志明"]):
        tags.append("#越南")
    if any(x in text for x in ["马来西亚", "吉隆坡"]):
        tags.append("#马来西亚")
    if any(x in text for x in ["菲律宾", "马尼拉"]):
        tags.append("#菲律宾")

    if any(x in text for x in ["诈骗", "电诈", "骗", "诈骗罪"]):
        tags.append("#诈骗")
    if any(x in text for x in ["警方", "警察", "公安", "抓捕", "通缉", "逮捕"]):
        tags.append("#警方通报")
    if any(x in text for x in ["突发", "现场", "曝光", "紧急"]):
        tags.append("#突发")

    if "#东南亚" not in tags:
        tags.insert(0, "#东南亚")
    if "#新闻" not in tags:
        tags.append("#新闻")

    seen = set()
    result = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            result.append(tag)

    return " ".join(result[:5])

# ========= 存储 =========
def load_hashes():
    if os.path.exists(DEDUP_FILE):
        try:
            with open(DEDUP_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.warning("读取去重文件失败: %s", e)
            return set()
    return set()

def save_hashes(hashes):
    with open(DEDUP_FILE, "w", encoding="utf-8") as f:
        json.dump(list(hashes), f, ensure_ascii=False, indent=2)

sent_hashes = load_hashes()

# ========= 清洗 =========
def clean_text(text):
    text = text or ""

    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"@\w+", "", text)
    text = re.sub(r"(?im)^.*(订阅|投稿|客服|联系|交友|关注那些事).*$", "", text)
    text = re.sub(r"#\w+\b", "", text)
    text = re.sub(r"[⚡🦑🔠👈👉❤️✈️👑📢👍💥🔥]", "", text)
    text = re.sub(r"\n{2,}", "\n", text)

    return text.strip()

# ========= 广告过滤 =========
def is_ad(text):
    text = (text or "").lower()

    hard = [
        "送彩金", "注册送", "邀请码", "下注", "上分",
        "娱乐城", "pg集团", "pg直营", "官方飞投",
        "盘口", "百家乐", "出款", ".cc", ".vip", ".com"
    ]
    for k in hard:
        if k in text:
            return True

    soft = [
        "盈利", "提款", "vip", "福利", "爆率",
        "资金", "贵宾厅", "红包", "活动",
        "首存", "返佣", "充值", "利润"
    ]
    hit = sum(1 for k in soft if k in text)
    if hit >= 3:
        return True

    if text.count("👍") >= 5:
        return True

    if text.count("u") >= 8:
        return True

    return False

# ========= 标题 =========
def get_title(text):
    if any(x in text for x in ["警方", "公安", "抓捕", "通缉", "遣返"]):
        return random.choice(["【警方通报】", "【最新情况】", "【刚刚通报】"])
    if any(x in text for x in ["突发", "现场", "曝光", "冲突", "起火", "枪击"]):
        return random.choice(["【突发】", "【现场曝光】", "【最新】"])
    if any(x in text for x in ["诈骗", "电诈", "园区"]):
        return random.choice(["【警惕】", "【曝光】", "【最新】"])
    return random.choice(["【最新】", "【关注】", "【东南亚快讯】"])

# ========= 爆款精简 =========
def make_hook(text):
    text = clean_text(text)

    if any(x in text for x in ["警方", "公安", "抓捕", "通缉", "遣返"]):
        return "👇 最新动态，评论区聊聊你怎么看"
    if any(x in text for x in ["诈骗", "电诈", "园区"]):
        return "👇 转发提醒身边人，小心踩坑"
    if any(x in text for x in ["冲突", "打架", "现场", "突发"]):
        return "👇 现场画面曝光，评论区聊聊"
    return "👇 评论区聊聊，你怎么看"

def trim_text(text):
    text = clean_text(text)
    if len(text) > 420:
        text = text[:420] + "..."
    return text

# ========= 去重 =========
def make_hash(text):
    text = clean_text(text)
    text = re.sub(r"^【.*?】", "", text)
    text = re.sub(r"[^\w\u4e00-\u9fa5]", "", text)
    text = re.sub(r"\s+", "", text)

    core = text[:120]
    raw = core + str(len(text))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

# ========= 文案 =========
def build_caption(text):
    cleaned = clean_text(text)
    short_text = trim_text(cleaned)
    tags = detect_tags(cleaned)
    title = get_title(cleaned)
    hook = make_hook(cleaned)

    return f"""{title}

{HEADER}

{short_text}

{hook}

{tags}

{FOOTER}"""

# ========= 延迟 =========
async def delay():
    t = random.randint(5, 12)
    logging.info("延迟 %s 秒", t)
    await asyncio.sleep(t)

# ========= 相册 =========
@client.on(events.Album(chats=SOURCE_CHATS))
async def album_handler(event):
    global sent_hashes

    try:
        msgs = event.messages
        text = "\n".join([m.raw_text or "" for m in msgs])

        if is_ad(text):
            logging.info("广告拦截（相册）")
            return

        files = []
        caption_text = ""

        for m in msgs:
            if m.media:
                files.append(m.media)
            if m.raw_text:
                caption_text += (m.raw_text + "\n")

        if not files:
            return

        h = make_hash(text)

        if h in sent_hashes:
            logging.info("重复相册，跳过")
            return

        await delay()
        await client.send_file(
            TARGET_CHAT,
            files,
            caption=build_caption(caption_text),
            force_document=False,
        )

        sent_hashes.add(h)
        save_hashes(sent_hashes)

        logging.info("已发相册")

    except Exception as e:
        logging.exception("相册错误: %s", e)

# ========= 单条 =========
@client.on(events.NewMessage(chats=SOURCE_CHATS))
async def handler(event):
    global sent_hashes

    try:
        msg = event.message

        if msg.grouped_id:
            return

        text = msg.raw_text or ""

        if not text.strip() and msg.media:
            text = "现场画面流出，更多情况持续关注。"

        if is_ad(text):
            logging.info("广告拦截（单条）")
            return

        h = make_hash(text)

        if h in sent_hashes:
            logging.info("重复消息，跳过")
            return

        await delay()

        if msg.media:
            await client.send_file(
                TARGET_CHAT,
                msg.media,
                caption=build_caption(text),
            )
        else:
            await client.send_message(
                TARGET_CHAT,
                build_caption(text),
            )

        sent_hashes.add(h)
        save_hashes(sent_hashes)

        logging.info("已发一条")

    except Exception as e:
        logging.exception("错误: %s", e)

async def main():
    await client.start()
    logging.info("运行中...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())