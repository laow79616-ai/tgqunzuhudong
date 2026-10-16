
import asyncio, json, time
from pathlib import Path
from sqlalchemy import select
from database import AsyncSessionLocal
from routers.framework import ProxyGroup, GroupSlot, PhotoItem
from models import Account, Proxy
from clients.manager import ClientManager

JOBS = Path("/opt/telegram_manager/profile_jobs.json")

def load_jobs():
    if not JOBS.exists():
        return []
    try:
        return json.loads(JOBS.read_text(encoding="utf-8"))
    except Exception:
        return []

def save_jobs(jobs):
    JOBS.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")

async def _apply(db, job, stage):
    gid = job["group_id"]
    g = await db.get(ProxyGroup, gid)
    if not g:
        return "组不存在"
    slots = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == gid).order_by(GroupSlot.slot, GroupSlot.pair_index))).scalars().all()
    photos = (await db.execute(select(PhotoItem).order_by(PhotoItem.used, PhotoItem.id))).scalars().all()
    ok = fail = skip = 0
    for i, s in enumerate(slots):
        acc = await db.get(Account, s.account_id)
        if not acc:
            skip += 1
            continue
        if (acc.health_status or "") == "frozen":
            skip += 1
            continue
        proxy = await db.get(Proxy, acc.proxy_id) if acc.proxy_id else None
        try:
            await ClientManager.start_client(acc.session_name, proxy_str=proxy.proxy_str if proxy else None)
            if stage == 1:
                from telethon.tl.functions.account import UpdateProfileRequest
                await ClientManager.get_client(acc.session_name)
                client = await ClientManager.start_client(acc.session_name, proxy_str=proxy.proxy_str if proxy else None)
                await client(UpdateProfileRequest(first_name=job["first_name"] or None, last_name=job["last_name"] or None))
                acc.name = f"{job['first_name']}{job['last_name']}"
            elif stage == 2:
                if photos:
                    photo = photos[i % len(photos)].path
                    await ClientManager.upload_profile_photo(acc.session_name, photo)
                    photos[i % len(photos)].used += 1
            elif stage == 3:
                from telethon.tl.functions.account import UpdateProfileRequest
                client = await ClientManager.start_client(acc.session_name, proxy_str=proxy.proxy_str if proxy else None)
                await client(UpdateProfileRequest(about=job["about"] or ""))
            ok += 1
        except Exception as e:
            msg = str(e)
            if "frozen" in msg.lower():
                acc.health_status = "frozen"
                skip += 1
            else:
                fail += 1
        await asyncio.sleep(45)
    await db.commit()
    return f"stage{stage} ok{ok} fail{fail} skip{skip}"

async def loop():
    print("profile scheduler started", flush=True)
    while True:
        jobs = load_jobs()
        now = time.time()
        changed = False
        for job in jobs:
            if job.get("status") == "done":
                continue
            for stage, key in ((1, "due1"), (2, "due2"), (3, "due3")):
                if job.get(f"done{stage}"):
                    continue
                if now >= job.get(key, 0):
                    async with AsyncSessionLocal() as db:
                        job[f"result{stage}"] = await _apply(db, job, stage)
                    job[f"done{stage}"] = True
                    changed = True
            if job.get("done1") and job.get("done2") and job.get("done3"):
                job["status"] = "done"
                changed = True
        if changed:
            save_jobs(jobs)
        print("profile tick", flush=True)
        await asyncio.sleep(30)

def start():
    asyncio.create_task(loop())
