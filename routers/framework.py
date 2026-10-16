"""代理组运行框架：22号、10群、1监听在线、文案库、冷却池、资料配图。"""
from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, Text, Boolean, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, Base

router = APIRouter(prefix="/framework", tags=["framework"])
PHOTO_DIR = Path("/opt/telegram_manager/uploads/avatars")
PHOTO_DIR.mkdir(parents=True, exist_ok=True)


class ProxyGroup(Base):
    __tablename__ = "fw_groups"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), default="")
    proxy_id = Column(Integer, nullable=True)
    first_name = Column(String(80), default="")
    last_name = Column(String(80), default="")
    about = Column(Text, default="")
    active_slot = Column(Integer, default=1)
    monitor_online = Column(Integer, default=1)
    created_at = Column(Integer, default=lambda: int(time.time()))


class GroupSlot(Base):
    """slot 1-10 互动，11 监听。pair_index 1 或 2。"""
    __tablename__ = "fw_slots"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, index=True)
    slot = Column(Integer)
    pair_index = Column(Integer, default=1)
    account_id = Column(Integer, nullable=True)
    link = Column(String(500), default="")
    online = Column(Boolean, default=False)


class PoolItem(Base):
    __tablename__ = "fw_pool"
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, index=True)
    state = Column(String(20), default="standby")  # standby / cooling
    cool_until = Column(Integer, default=0)
    from_group_id = Column(Integer, nullable=True)
    from_slot = Column(Integer, nullable=True)


class CopyLine(Base):
    __tablename__ = "fw_copies"
    id = Column(Integer, primary_key=True)
    text = Column(Text, default="")
    source_group = Column(String(200), default="")
    is_bot = Column(Boolean, default=False)
    created_at = Column(Integer, default=lambda: int(time.time()))


class PhotoItem(Base):
    __tablename__ = "fw_photos"
    id = Column(Integer, primary_key=True)
    path = Column(String(400))
    used = Column(Integer, default=0)


class GroupIn(BaseModel):
    name: str = ""
    proxy_id: int
    account_ids: list[int] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class ProfileIn(BaseModel):
    group_ids: list[int]
    first_name: str = ""
    last_name: str = ""
    about: str = ""


class PoolIn(BaseModel):
    account_id: int
    state: str = "standby"
    minutes: int = 0


def grams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s or "")
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similarity(a: str, b: str) -> float:
    ga, gb = grams(a), grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


@router.post("/groups")
async def create_group(req: GroupIn, db: AsyncSession = Depends(get_db)):
    import asyncio
    for _try in range(5):
        try:
            return await _create_group_inner(req, db)
        except Exception as e:
            await db.rollback()
            if "locked" in str(e) and _try < 4:
                await asyncio.sleep(1.5)
                continue
            raise

async def _create_group_inner(req: GroupIn, db: AsyncSession):
    ids = list(dict.fromkeys(req.account_ids))
    links = [x.strip() for x in req.links if x and x.strip()]
    if len(ids) != 22:
        raise HTTPException(400, f"一组必须 22 个号，当前 {len(ids)}")
    if len(links) != 10:
        raise HTTPException(400, f"一组必须 10 个群，当前 {len(links)}")
    g = ProxyGroup(name=req.name or "代理组", proxy_id=req.proxy_id)
    db.add(g)
    await db.flush()
    n = 0
    for slot in range(1, 11):
        for pair in (1, 2):
            db.add(GroupSlot(group_id=g.id, slot=slot, pair_index=pair, account_id=ids[n], link=links[slot - 1]))
            n += 1
    for pair in (1, 2):
        db.add(GroupSlot(group_id=g.id, slot=11, pair_index=pair, account_id=ids[n], link=""))
        n += 1
    await db.commit()
    return {"id": g.id, "accounts": 22, "groups": 10, "monitor": 2}


@router.get("/groups")
async def list_groups(db: AsyncSession = Depends(get_db)):
    groups = (await db.execute(select(ProxyGroup).order_by(ProxyGroup.id))).scalars().all()
    out = []
    for g in groups:
        slots = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == g.id))).scalars().all()
        online = [s.account_id for s in slots if s.online and s.slot <= 10]
        out.append({
            "id": g.id, "name": g.name, "proxy_id": g.proxy_id,
            "active_slot": g.active_slot, "monitor_online": g.monitor_online,
            "slots": len(slots), "online_accounts": online,
            "first_name": g.first_name, "last_name": g.last_name, "about": g.about,
        })
    return out


@router.delete("/groups/{gid}")
async def delete_group(gid: int, db: AsyncSession = Depends(get_db)):
    slots = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == gid))).scalars().all()
    for s in slots:
        await db.delete(s)
    g = await db.get(ProxyGroup, gid)
    if g:
        await db.delete(g)
    await db.commit()
    return {"ok": True, "deleted": gid}


@router.post("/groups/{gid}/online")
async def set_online(gid: int, slot: int, db: AsyncSession = Depends(get_db)):
    """一组只上线指定槽位的 2 个互动号。"""
    g = await db.get(ProxyGroup, gid)
    if not g:
        raise HTTPException(404, "组不存在")
    slots = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == gid))).scalars().all()
    for s in slots:
        if s.slot <= 10:
            s.online = s.slot == slot
    g.active_slot = slot
    await db.commit()
    return {"active_slot": slot, "online": 2}


@router.post("/groups/{gid}/monitor")
async def set_monitor(gid: int, pair_index: int, db: AsyncSession = Depends(get_db)):
    """监听只保持 1 个在线。"""
    if pair_index not in (1, 2):
        raise HTTPException(400, "监听只能是 1 或 2")
    g = await db.get(ProxyGroup, gid)
    if not g:
        raise HTTPException(404, "组不存在")
    slots = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == gid, GroupSlot.slot == 11))).scalars().all()
    for s in slots:
        s.online = s.pair_index == pair_index
    g.monitor_online = pair_index
    await db.commit()
    return {"monitor_online": pair_index}


@router.post("/profile")
async def sync_profile(req: ProfileIn, db: AsyncSession = Depends(get_db)):
    photos = (await db.execute(select(PhotoItem).order_by(PhotoItem.used))).scalars().all()
    done = []
    for gid in req.group_ids:
        g = await db.get(ProxyGroup, gid)
        if not g:
            continue
        g.first_name = req.first_name
        g.last_name = req.last_name
        g.about = req.about
        slots = (await db.execute(select(GroupSlot).where(GroupSlot.group_id == gid))).scalars().all()
        for i, s in enumerate(slots):
            username = f"{(req.first_name or 'u').lower()}{gid}_{s.slot}{s.pair_index}"
            photo = photos[i % len(photos)].path if photos else ""
            if photos:
                photos[i % len(photos)].used += 1
            done.append({"group_id": gid, "account_id": s.account_id, "username": username, "photo": photo})
    await db.commit()
    return {"synced": len(done), "items": done}


@router.post("/photos")
async def add_photo(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    import re
    safe = re.sub(r"[^\w.\-]", "_", file.filename or "img.jpg")
    name = f"{int(time.time()*1000)}_{safe}"
    path = PHOTO_DIR / name
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    path.write_bytes(data)
    db.add(PhotoItem(path=str(path)))
    await db.commit()
    return {"path": str(path), "name": name}


@router.get("/photos")
async def list_photos(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(PhotoItem))).scalars().all()
    return [{"id": r.id, "path": r.path, "used": r.used} for r in rows]


@router.post("/pool")
async def add_pool(req: PoolIn, db: AsyncSession = Depends(get_db)):
    until = int(time.time()) + req.minutes * 60 if req.state == "cooling" else 0
    db.add(PoolItem(account_id=req.account_id, state=req.state, cool_until=until))
    await db.commit()
    return {"ok": True}


@router.get("/pool")
async def list_pool(db: AsyncSession = Depends(get_db)):
    now = int(time.time())
    rows = (await db.execute(select(PoolItem).order_by(PoolItem.id))).scalars().all()
    return [{
        "id": r.id, "account_id": r.account_id, "state": r.state,
        "ready": r.state != "cooling" or r.cool_until <= now,
        "cool_left": max(0, r.cool_until - now),
    } for r in rows]


@router.post("/groups/{gid}/slots/{slot}/replace")
async def replace_slot(gid: int, slot: int, db: AsyncSession = Depends(get_db)):
    now = int(time.time())
    pool = (await db.execute(select(PoolItem).order_by(PoolItem.id))).scalars().all()
    nxt = next((p for p in pool if p.state == "standby" or (p.state == "cooling" and p.cool_until <= now)), None)
    if not nxt:
        raise HTTPException(400, "水军池没有可接上的号")
    slot_row = (await db.execute(
        select(GroupSlot).where(GroupSlot.group_id == gid, GroupSlot.slot == slot, GroupSlot.account_id.is_(None))
    )).scalars().first()
    if not slot_row:
        slot_row = (await db.execute(
            select(GroupSlot).where(GroupSlot.group_id == gid, GroupSlot.slot == slot).order_by(GroupSlot.pair_index)
        )).scalars().first()
    if not slot_row:
        raise HTTPException(404, "槽位不存在")
    slot_row.account_id = nxt.account_id
    await db.delete(nxt)
    g = await db.get(ProxyGroup, gid)
    await db.commit()
    return {
        "account_id": slot_row.account_id,
        "slot": slot,
        "profile": {"first_name": g.first_name, "last_name": g.last_name, "about": g.about},
    }


@router.post("/groups/{gid}/dead")
async def mark_dead(gid: int, account_id: int, db: AsyncSession = Depends(get_db)):
    slots = (await db.execute(
        select(GroupSlot).where(GroupSlot.group_id == gid, GroupSlot.account_id == account_id)
    )).scalars().all()
    for s in slots:
        s.account_id = None
        s.online = False
    await db.commit()
    return {"deleted_from_slot": True, "account_id": account_id}


@router.post("/copies")
async def add_copy(text: str, source_group: str = "", db: AsyncSession = Depends(get_db)):
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "空文案")
    db.add(CopyLine(text=text, source_group=source_group, is_bot=False))
    await db.commit()
    return {"ok": True}



@router.post("/copies/import")
async def import_copies(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    raw = (await file.read()).decode("utf-8", errors="ignore")
    lines = [x.strip() for x in raw.replace("\r", "\n").split("\n") if x.strip()]
    n = 0
    for line in lines:
        if len(line) < 2:
            continue
        db.add(CopyLine(text=line, source_group="本地导入", is_bot=False))
        n += 1
    await db.commit()
    return {"imported": n, "file": file.filename}

@router.get("/copies")
async def list_copies(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CopyLine).order_by(CopyLine.id.desc()).limit(200))).scalars().all()
    return [{"id": r.id, "text": r.text, "source_group": r.source_group, "created_at": r.created_at} for r in rows]


@router.post("/match")
async def match_copy(context: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(CopyLine).where(CopyLine.is_bot.is_(False)))).scalars().all()
    scored = []
    for r in rows:
        score = similarity(context, r.text)
        if score > 0:
            scored.append({"id": r.id, "text": r.text, "score": round(score, 4), "source_group": r.source_group})
    scored.sort(key=lambda x: x["score"], reverse=True)
    if not scored:
        return {"reply": "哈哈是的", "score": 0, "fallback": True}
    best = scored[0]
    if best["score"] < 0.08:
        return {"reply": "确实", "score": best["score"], "fallback": True}
    return {"reply": best["text"], "score": best["score"], "fallback": False, "from": best["source_group"]}


@router.post("/profile/schedule")
async def schedule_profile(req: ProfileIn):
    import time, json
    from pathlib import Path
    p = Path("/opt/telegram_manager/profile_jobs.json")
    jobs = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    now = time.time()
    created = []
    for gid in req.group_ids:
        jobs = [j for j in jobs if not (j.get("group_id") == gid and j.get("status") != "done")]
        job = {
            "group_id": gid,
            "first_name": req.first_name,
            "last_name": req.last_name,
            "about": req.about,
            "due1": now + 60,
            "due2": now + 10 * 3600,
            "due3": now + 20 * 3600,
            "done1": False, "done2": False, "done3": False,
            "status": "scheduled",
        }
        jobs.append(job)
        created.append(gid)
    p.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"scheduled": created, "plan": "1小时内改名字，10小时后改头像，再10小时后改简介"}

@router.post("/reply/start")
async def reply_start():
    Path("/opt/telegram_manager/reply_enabled.txt").write_text("1", encoding="utf-8")
    return {"ok": True, "reply": True}

@router.post("/reply/stop")
async def reply_stop():
    Path("/opt/telegram_manager/reply_enabled.txt").write_text("0", encoding="utf-8")
    return {"ok": True, "reply": False}

@router.get("/reply/status")
async def reply_status():
    on = Path("/opt/telegram_manager/reply_enabled.txt").read_text().strip() != "0"
    return {"reply": on}
