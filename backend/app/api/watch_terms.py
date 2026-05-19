import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.ingestion.scheduler import poll_once
from app.models import WatchTerm
from app.schemas import WatchTermCreate, WatchTermOut, WatchTermUpdate

router = APIRouter(prefix="/api/watch-terms", tags=["watch-terms"])


@router.get("/", response_model=list[WatchTermOut])
def list_terms(db: Session = Depends(get_db)):
    return db.query(WatchTerm).order_by(WatchTerm.created_at.desc()).all()


@router.post("/", response_model=WatchTermOut, status_code=201)
async def create_term(body: WatchTermCreate, db: Session = Depends(get_db)):
    term = WatchTerm(**body.model_dump())
    db.add(term)
    db.commit()
    db.refresh(term)
    asyncio.create_task(poll_once())
    return term


@router.patch("/{term_id}", response_model=WatchTermOut)
def update_term(term_id: int, body: WatchTermUpdate, db: Session = Depends(get_db)):
    term = db.get(WatchTerm, term_id)
    if not term:
        raise HTTPException(404, "Watch term not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(term, k, v)
    db.commit()
    db.refresh(term)
    return term


@router.delete("/{term_id}", status_code=204)
def delete_term(term_id: int, db: Session = Depends(get_db)):
    term = db.get(WatchTerm, term_id)
    if not term:
        raise HTTPException(404, "Watch term not found")
    db.delete(term)
    db.commit()
