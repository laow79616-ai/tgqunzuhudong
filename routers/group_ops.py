from __future__ import annotations
import re, time
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, String, Text, select, delete
from sqlalchemy.orm import declarative_base
from database import get_db
try:
    from models import Base
except Exception:
    Base = declarative_base()

class ArmyGroup(Base):
    __tablename__ = "army_groups"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), default="")
    proxy_id = Column(Integer, nullable=True)
    created_at = Column(Integer, default=lambda: int(time.time()))

class ArmyMember(Base):
    __tablename__ = "army_members"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, index=True)
    account_id = Column(Integer, index=True)
    slot = Column(Integer, default=0)

class TargetBundle(Base):
    __tablename__ = "target_bundles"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), default="")
    created_at = Column(Integer, default=lambda: int(time.time()))

class TargetBundleItem(Base):
    __tablename__ = "target_bundle_items"
    id = Column(Integer, primary_key=True)
    bundle_id = Column(Integer, index=True)
    link = Column(String(500), default="")
    slot = Column(Integer, default=0)

class CopyItem(Base):
    __tablename__ = "copy_items"
    id = Column(Integer, primary_key=True)
    text = Column(Text, default="")
    source = Column(String(300), default="")
    created_at = Column(Integer, default=lambda: int(time.time()))

class JoinPlan(Base):
    __tablename__ = "join_plans"
    id = Column(Integer, primary_key=True)
    army_group_id = Column(Integer, index=True)
    bundle_id = Column(Integer, index=True)
    account_id = Column(Integer)
    link = Column(String(500), default="")
    status = Column(String(30), default="pending")
    created_at = Column(Integer, default=lambda: int(time.time()))

router = APIRouter(prefix="/group-ops", tags=["group-ops"])

class ArmyIn(BaseModel):
    name: str = ""
    proxy_id: int
    account_ids: list[int] = Field(default_factory=list)

class BundleIn(BaseModel):
    name: str = ""
    links: list[str] = Field(default_factory=list)

class MapIn(BaseModel):
    army_group_id: int
    bundle_id: int

class CopyIn(BaseModel):
    text: str
    source: str = ""

class MatchIn(BaseModel):
    text: str
    top: int = 3

def _grams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s or "")
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i+2] for i in range(len(s)-1)}

def similarity(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)

async def ensure_tables(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@router.post("/army")
async def create_army(req: ArmyIn, db=Depends(get_db)):
    ids = list(dict.fromkeys(req.account_ids))
    if len(ids) != 20:
        raise HTTPException(400, f"一个水军组必须固定 20 个号，当前 {len(ids)}")
    g = ArmyGroup(name=req.name or "水军组", proxy_id=req.proxy_id)
    db.add(g)
    await db.flush()
    for i, aid in enumerate(ids, 1):
        db.add(ArmyMember(group_id=g.id, account_id=aid, slot=i))
    await db.commit()
    return {"id": g.id, "proxy_id": req.proxy_id, "accounts": 20}

@router.get("/army")
async def list_army(db=Depends(get_db)):
    groups = (await db.execute(select(ArmyGroup).order_by(ArmyGroup.id))).scalars().all()
    out = []
    for g in groups:
        members = (await db.execute(select(ArmyMember).where(ArmyMember.group_id == g.id))).scalars().all()
        out.append({"id": g.id, "name": g.name, "proxy_id": g.proxy_id, "accounts": [m.account_id for m in members]})
    return out

@router.post("/bundles")
async def create_bundle(req: BundleIn, db=Depends(get_db)):
    links = [x.strip() for x in req.links if x and x.strip()]
    if len(links) != 10:
        raise HTTPException(400, f"一个目标组必须 10 个群，当前 {len(links)}")
    b = TargetBundle(name=req.name or "目标组")
    db.add(b)
    await db.flush()
    for i, link in enumerate(links, 1):
        db.add(TargetBundleItem(bundle_id=b.id, link=link, slot=i))
    await db.commit()
    return {"id": b.id, "groups": 10}

@router.get("/bundles")
async def list_bundles(db=Depends(get_db)):
    bundles = (await db.execute(select(TargetBundle).order_by(TargetBundle.id))).scalars().all()
    out = []
    for b in bundles:
        items = (await db.execute(select(TargetBundleItem).where(TargetBundleItem.bundle_id == b.id))).scalars().all()
        out.append({"id": b.id, "name": b.name, "links": [it.link for it in items]})
    return out

@router.post("/join-map")
async def join_map(req: MapIn, db=Depends(get_db)):
    members = (await db.execute(select(ArmyMember).where(ArmyMember.group_id == req.army_group_id).order_by(ArmyMember.slot))).scalars().all()
    items = (await db.execute(select(TargetBundleItem).where(TargetBundleItem.bundle_id == req.bundle_id).order_by(TargetBundleItem.slot))).scalars().all()
    if len(members) != 20 or len(items) != 10:
        raise HTTPException(400, "水军组必须 20 号，目标组必须 10 群")
    plan = []
    for i, item in enumerate(items):
        for m in members[i*2:i*2+2]:
            db.add(JoinPlan(army_group_id=req.army_group_id, bundle_id=req.bundle_id, account_id=m.account_id, link=item.link))
            plan.append({"account_id": m.account_id, "link": item.link})
    await db.commit()
    return {"pairs": len(plan), "per_group": 2, "plan": plan}

@router.post("/copies/bulk")
async def add_copies(lines: list[str], db=Depends(get_db)):
    n = 0
    for line in lines:
        text = (line or "").strip()
        if text:
            db.add(CopyItem(text=text))
            n += 1
    await db.commit()
    return {"added": n}

@router.post("/reply-plan")
async def reply_plan(req: MatchIn, db=Depends(get_db)):
    rows = (await db.execute(select(CopyItem))).scalars().all()
    scored = sorted(({"id": r.id, "text": r.text, "score": round(similarity(req.text, r.text), 4)} for r in rows), key=lambda x: x["score"], reverse=True)
    best = next((x for x in scored if x["score"] > 0), None)
    return {"reply": best, "gap_seconds": 10, "alts": [x for x in scored if x is not best][:2]}
