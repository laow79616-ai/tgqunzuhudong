import asyncio, json, re, time
from pathlib import Path
from sqlalchemy import select
from database import AsyncSessionLocal
from models import Account, Proxy
from routers.framework import GroupSlot, CopyLine, similarity
from clients.manager import ClientManager

STATE = Path("/opt/telegram_manager/join_state.json")

def load_state():
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

def classify(msg: str):
    m = msg or ""
    if "加入成功" in m or "已经是成员" in m:
        return "joined", 0
    if "requested to join" in m or "申请" in m:
        return "pending", 0
    sec = re.search(r"(\d+)\s*秒", m)
    if sec:
        return "wait", int(sec.group(1))
    return "retry", 120

async def _accounts():
    async with AsyncSessionLocal() as db:
        slots = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == 1, GroupSlot.link != ""))).scalars().all()
        out = []
        for s in slots:
            acc = await db.get(Account, s.account_id)
            if not acc:
                continue
            proxy = await db.get(Proxy, acc.proxy_id) if acc.proxy_id else None
            out.append({"slot": s.slot, "pair": s.pair_index, "session": acc.session_name, "phone": acc.phone, "link": s.link, "proxy": proxy.proxy_str if proxy else None})
        return out

async def ensure_joined():
    rows = await _accounts()
    st = load_state()
    now = time.time()
    for r in rows:
        key = f"{r['session']}|{r['link']}"
        cur = st.get(key, {})
        if cur.get("status") in ("joined", "pending"):
            continue
        if cur.get("retry_at", 0) > now:
            continue
        try:
            await ClientManager.start_client(r["session"], proxy_str=r["proxy"])
            res = await ClientManager.join_group_or_channel(r["session"], r["link"])
            status, wait = classify(res.get("msg", ""))
            st[key] = {"status": status, "retry_at": now + wait, "msg": res.get("msg", ""), "slot": r["slot"]}
            print("autojoin", r["phone"], r["link"], status, res.get("msg"), flush=True)
        except Exception as e:
            st[key] = {"status": "retry", "retry_at": now + 180, "msg": str(e)[:120], "slot": r["slot"]}
            print("autojoin fail", r["phone"], e, flush=True)
        save_state(st)
        await asyncio.sleep(8)


async def drop_and_backfill(link: str):
    """不能发言的群删除，并用列表最后一条补到原位置。"""
    from models import Target
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Target).order_by(Target.id))).scalars().all()
        cur = next((r for r in rows if r.link == link), None)
        if not cur:
            return
        last = rows[-1] if rows else None
        slots = (await db.execute(select(GroupSlot).where(GroupSlot.link == link))).scalars().all()
        if last and last.id != cur.id:
            new_link, new_name = last.link, last.name
            await db.delete(last)
            await db.flush()
            cur.link, cur.name = new_link, new_name
            for s in slots:
                s.link = new_link
            print("backfill", link, "<-", new_link, flush=True)
        else:
            for s in slots:
                s.link = ""
            await db.delete(cur)
            print("drop last", link, flush=True)
        await db.commit()
    st = load_state()
    for k in list(st):
        if k.endswith("|" + link):
            st.pop(k, None)
    save_state(st)


AD_WORDS = ("收量","广告","代发","出号","接广","CPA","CPS","wip","WIP","@","http","t.me","价格","招代理","轰炸","签名","实名","日赚","无限收","出私","微信","无视","IP","收各种","炮台","上压","代","广","量","号","私","质量","联系","加我","私聊")
def is_chat(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 2 or len(s) > 24:
        return False
    if any(w in s for w in AD_WORDS):
        return False
    if sum(ch.isdigit() for ch in s) >= 3:
        return False
    return True

def reply_on():
    try:
        return Path("/opt/telegram_manager/reply_enabled.txt").read_text().strip() != "0"
    except Exception:
        return True

async def interact_once():
    if not reply_on():
        return
    async with AsyncSessionLocal() as db:
        recent = (await db.execute(select(CopyLine).order_by(CopyLine.id.desc()).limit(20))).scalars().all()
        slots = (await db.execute(select(GroupSlot).where(GroupSlot.slot <= 10))).scalars().all()
        copies = (await db.execute(select(CopyLine))).scalars().all()
    if not recent:
        return
    human = [r for r in recent if r.source_group and not str(r.source_group).startswith("水军回复") and is_chat(r.text)]
    if not human:
        return
    src = human[0].source_group
    text = human[0].text
    # match slot by link title loosely: use slot whose link username appears, else slot 1
    target = None
    for s in slots:
        if s.link and s.link.rsplit("/", 1)[-1].lower() in (src or "").lower():
            target = s
            break
    by_group = {}
    for s in slots:
        by_group.setdefault(s.group_id, []).append(s)
    pairs = []
    for gid, gs in by_group.items():
        target = None
        for s in gs:
            if s.link and s.link.rsplit("/", 1)[-1].lower() in (src or "").lower():
                target = s
                break
        slot_no = target.slot if target else 1
        pair = [s for s in gs if s.slot == slot_no][:2]
        if len(pair) >= 1:
            pairs.extend(pair)
    pair = pairs
    if not pair:
        return
    NORMAL = ["哈哈是的","对啊","确实","我也这么觉得","一直都在","有点意思","说得对","嗯嗯","是啊","挺好的","我也是","可以啊","哈哈","还行","明白"]
    pool = [c.text for c in copies if c.text and is_chat(c.text) and not (c.source_group or "").startswith("水军回复")]
    pool = list(dict.fromkeys(pool + NORMAL))
    best, score = "", 0
    for c in pool:
        if c.strip() == text.strip() or similarity(c, text) > 0.75:
            continue
        sc = similarity(text, c)
        if sc > score:
            best, score = c, sc
    if not best:
        best = "哈哈是的"
    st = load_state()
    async with AsyncSessionLocal() as db:
        for s in pair:
            acc = await db.get(Account, s.account_id)
            if not acc:
                continue
            key = f"{acc.session_name}|{s.link}"
            if st.get(key, {}).get("status") != "joined":
                continue
            proxy = await db.get(Proxy, acc.proxy_id) if acc.proxy_id else None
            try:
                await ClientManager.start_client(acc.session_name, proxy_str=proxy.proxy_str if proxy else None)
                await ClientManager.send_message(acc.session_name, s.link, best[:180])
                print("reply", acc.phone, s.link, best[:30], flush=True)
                async with AsyncSessionLocal() as db2:
                    db2.add(CopyLine(text=best[:180], source_group=f"水军回复 {acc.name or acc.phone} -> {s.link}", is_bot=True, created_at=int(time.time())))
                    await db2.commit()
            except Exception as e:
                msg = str(e)
                print("reply fail", acc.phone, msg, flush=True)
                if "can't write" in msg or "cannot send plain" in msg or "CHAT_WRITE_FORBIDDEN" in msg or "CHAT_SEND_PLAIN_FORBIDDEN" in msg:
                    await drop_and_backfill(s.link)
            await asyncio.sleep(10)

async def loop():
    print("group runner started", flush=True)
    while True:
        try:
            await ensure_joined()
            await interact_once()
        except Exception as e:
            print("runner error", e, flush=True)
        await asyncio.sleep(30)

def start():
    asyncio.create_task(loop())
