import asyncio, time
from telethon import events
from sqlalchemy import select
from database import AsyncSessionLocal
from models import Account, Proxy
from routers.framework import GroupSlot, CopyLine
from clients.manager import ClientManager

LINKS = [
    "https://t.me/CPA_SEO_CDN_PHP",
    "https://t.me/wwwxzz666net",
    "https://t.me/bfp6666",
    "https://t.me/hmseoba",
    "https://t.me/seokk777",
    "https://t.me/HostCLiCOM0",
    "https://t.me/dfuux",
    "https://t.me/duoduo_88888888",
    "https://t.me/AUZ88881",
    "https://t.me/EkHJ_VV_L1ZlOGY9",
]

async def _save(text, source):
    async with AsyncSessionLocal() as db:
        db.add(CopyLine(text=text[:500], source_group=source[:200], is_bot=False, created_at=int(time.time())))
        await db.commit()

def _bind(client, session_name):
    if getattr(client, "_fw_listen", False):
        return
    @client.on(events.NewMessage(incoming=True))
    async def on_msg(event):
        text = (event.message.message or "").strip()
        if not text or len(text) < 2:
            return
        sender = await event.get_sender()
        if sender and getattr(sender, "bot", False):
            return
        chat = await event.get_chat()
        source = getattr(chat, "title", None) or getattr(chat, "username", None) or str(getattr(event, "chat_id", ""))
        try:
            await _save(text, str(source))
            print(f"[listen] {source}: {text[:40]}", flush=True)
        except Exception as e:
            print("listen save fail", e, flush=True)
    client._fw_listen = True

async def start_monitor():
    async with AsyncSessionLocal() as db:
        slot = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == 1, GroupSlot.slot == 11, GroupSlot.pair_index == 1))).scalar_one_or_none()
        if not slot:
            print("no monitor slot", flush=True)
            return
        acc = await db.get(Account, slot.account_id)
        proxy = await db.get(Proxy, acc.proxy_id) if acc and acc.proxy_id else None
    if not acc:
        print("monitor account missing", flush=True)
        return
    client = await ClientManager.start_client(acc.session_name, proxy_str=proxy.proxy_str if proxy else None)
    _bind(client, acc.session_name)
    print("monitor online", acc.phone, flush=True)
    for link in LINKS:
        try:
            res = await ClientManager.join_group_or_channel(acc.session_name, link)
            print("join", link, res, flush=True)
        except Exception as e:
            print("join fail", link, e, flush=True)
        await asyncio.sleep(8)
    print("monitor joined", flush=True)

def start():
    asyncio.create_task(start_monitor())
