"""
main.py - The FastAPI web server. It connects everything together:

  upload bill -> read it (OCR) -> pick category (AI) -> run policy checks
  -> save file (Blob) -> save claim (DB) -> HR approves/rejects

It also serves the frontend (app/static) at "/".
"""
import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import settings
from .db import Claim, RebalanceRequest, get_db, init_db
from .services import benefits, classifier, ocr, rules, storage

ALLOWED_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create tables on startup
    yield


app = FastAPI(title="ReimburseAI - AI Reimbursement Bill Scanner", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health():
    """Used by the CI/CD pipeline to check the deployment worked."""
    return {
        "status": "ok",
        "azure_ocr": settings.use_azure_ocr,
        "azure_openai": settings.use_azure_openai,
        "azure_storage": settings.use_azure_storage,
    }


@app.post("/api/claims", status_code=201)
def submit_claim(
    employee_name: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Employee uploads a bill. We scan, classify, check and save it."""
    employee_name = employee_name.strip()
    if not employee_name:
        raise HTTPException(400, "Enter your name before uploading a bill.")
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "Upload a JPG, PNG or PDF bill.")

    data = file.file.read()
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(413, "The bill must be smaller than 5 MB.")

    # Fingerprint of the file, used for duplicate detection
    file_hash = hashlib.sha256(data).hexdigest()

    # Step 1: read the bill
    try:
        extracted = ocr.extract_receipt(data)
    except Exception as exc:
        raise HTTPException(502, f"The bill couldn't be scanned: {exc}")

    # Step 2: decide the category
    category, reason = classifier.classify(extracted)

    # Step 3: policy + fraud checks against earlier claims
    earlier = db.scalars(
        select(Claim).where(or_(Claim.employee_name == employee_name, Claim.file_hash == file_hash))
    ).all()
    bill_month = (extracted.get("date") or benefits.current_month())[:7]
    limits = benefits.effective_limits(employee_name, bill_month, _moves_of(db, employee_name))
    flags = rules.check_flags(
        extracted, category, employee_name, file_hash, [c.to_dict() for c in earlier],
        limits=limits,
    )

    # Step 4: store the original file
    blob_name = storage.save_bill(data, file_hash, file.content_type)

    # Step 5: save the claim
    claim = Claim(
        employee_name=employee_name,
        vendor=extracted.get("vendor"),
        bill_date=extracted.get("date"),
        amount=extracted.get("total"),
        currency=extracted.get("currency") or "INR",
        items=json.dumps(extracted.get("items") or []),
        ocr_confidence=extracted.get("confidence"),
        category=category,
        ai_reason=reason,
        flags=json.dumps(flags),
        file_hash=file_hash,
        blob_name=blob_name,
        content_type=file.content_type,
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)
    return claim.to_dict()


@app.get("/api/claims")
def list_claims(status: str | None = None, db: Session = Depends(get_db)):
    query = select(Claim).order_by(Claim.created_at.desc())
    if status:
        query = query.where(Claim.status == status)
    return [c.to_dict() for c in db.scalars(query).all()]


def _get_claim(claim_id: int, db: Session) -> Claim:
    claim = db.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(404, f"Claim #{claim_id} doesn't exist.")
    return claim


@app.get("/api/claims/{claim_id}")
def get_claim(claim_id: int, db: Session = Depends(get_db)):
    return _get_claim(claim_id, db).to_dict()


@app.get("/api/claims/{claim_id}/bill")
def get_bill_file(claim_id: int, db: Session = Depends(get_db)):
    """Streams the original bill so HR can view it (Blob stays private)."""
    claim = _get_claim(claim_id, db)
    return Response(storage.load_bill(claim.blob_name), media_type=claim.content_type)


class Decision(BaseModel):
    status: Literal["approved", "rejected"]
    comment: str = ""


@app.post("/api/claims/{claim_id}/decision")
def decide_claim(claim_id: int, decision: Decision, db: Session = Depends(get_db)):
    """HR approves or rejects a claim."""
    claim = _get_claim(claim_id, db)
    if claim.status != "pending":
        raise HTTPException(409, f"Claim #{claim_id} was already {claim.status}.")
    if decision.status == "rejected" and not decision.comment.strip():
        raise HTTPException(400, "Add a comment explaining why the claim is rejected.")
    claim.status = decision.status
    claim.hr_comment = decision.comment.strip()
    db.commit()
    return claim.to_dict()


@app.get("/api/summary")
def summary(db: Session = Depends(get_db)):
    """Numbers for the HR dashboard."""
    claims = db.scalars(select(Claim)).all()
    by_category: dict[str, float] = {}
    for c in claims:
        if c.status == "approved" and c.amount:
            by_category[c.category] = by_category.get(c.category, 0) + c.amount
    pending_moves = db.scalars(select(RebalanceRequest).where(RebalanceRequest.status == "pending")).all()
    return {
        "pending_rebalances": len(pending_moves),
        "pending": sum(1 for c in claims if c.status == "pending"),
        "flagged_pending": sum(1 for c in claims if c.status == "pending" and json.loads(c.flags)),
        "approved_amount": round(sum(by_category.values()), 2),
        "approved_by_category": by_category,
    }


# ======================= Smart Benefit Rebalancer =======================

def _claims_of(db: Session, employee: str) -> list[dict]:
    return [c.to_dict() for c in db.scalars(select(Claim).where(Claim.employee_name == employee))]


def _moves_of(db: Session, employee: str) -> list[dict]:
    rows = db.scalars(select(RebalanceRequest).where(RebalanceRequest.employee_name == employee))
    return [m.to_dict() for m in rows]


@app.get("/api/benefits")
def get_benefits(employee: str, db: Session = Depends(get_db)):
    """Employee's live balance for this month, per category."""
    employee = employee.strip()
    if not employee:
        raise HTTPException(400, "Enter your name to see your benefits.")
    month = benefits.current_month()
    rows = benefits.balance(employee, month, _claims_of(db, employee), _moves_of(db, employee))
    return {
        "employee": employee,
        "month": month,
        "total_budget": benefits.total_budget(),
        "categories": list(rows.values()),
    }


class MoveRequest(BaseModel):
    employee_name: str = Field(min_length=1, max_length=120)
    from_category: str
    to_category: str
    amount: float = Field(gt=0)
    reason: str = Field(min_length=3, max_length=300)


@app.post("/api/rebalance", status_code=201)
def request_rebalance(body: MoveRequest, db: Session = Depends(get_db)):
    """Employee asks to move unused limit from one category to another."""
    employee = body.employee_name.strip()
    month = benefits.current_month()
    error = benefits.validate_move(
        employee, month, body.from_category, body.to_category, body.amount,
        _claims_of(db, employee), _moves_of(db, employee),
    )
    if error:
        raise HTTPException(400, error)
    move = RebalanceRequest(
        employee_name=employee, month=month,
        from_category=body.from_category, to_category=body.to_category,
        amount=round(body.amount, 2), reason=body.reason.strip(),
    )
    db.add(move)
    db.commit()
    db.refresh(move)
    return move.to_dict()


@app.get("/api/rebalance")
def list_rebalances(status: str | None = None, employee: str | None = None,
                    db: Session = Depends(get_db)):
    query = select(RebalanceRequest).order_by(RebalanceRequest.created_at.desc())
    if status:
        query = query.where(RebalanceRequest.status == status)
    if employee:
        query = query.where(RebalanceRequest.employee_name == employee.strip())
    result = []
    for m in db.scalars(query).all():
        item = m.to_dict()
        if m.status == "pending":
            # Show HR how much is still free in the source category right now
            bal = benefits.balance(m.employee_name, m.month, _claims_of(db, m.employee_name),
                                   _moves_of(db, m.employee_name), ignore_move_id=m.id)
            item["available_now"] = bal[m.from_category]["available"]
        result.append(item)
    return result


@app.post("/api/rebalance/{move_id}/decision")
def decide_rebalance(move_id: int, decision: Decision, db: Session = Depends(get_db)):
    """HR approves or rejects a limit change."""
    move = db.get(RebalanceRequest, move_id)
    if move is None:
        raise HTTPException(404, f"Request #{move_id} doesn't exist.")
    if move.status != "pending":
        raise HTTPException(409, f"Request #{move_id} was already {move.status}.")
    if decision.status == "rejected" and not decision.comment.strip():
        raise HTTPException(400, "Add a comment explaining why the request is rejected.")
    if decision.status == "approved":
        # Check again: the employee may have claimed more bills since asking
        error = benefits.validate_move(
            move.employee_name, move.month, move.from_category, move.to_category, move.amount,
            _claims_of(db, move.employee_name), _moves_of(db, move.employee_name),
            ignore_move_id=move.id,
        )
        if error:
            raise HTTPException(409, f"This request can't be approved anymore. {error}")
    move.status = decision.status
    move.hr_comment = decision.comment.strip()
    db.commit()
    return move.to_dict()


# Frontend (must be mounted last so it doesn't hide the /api routes)
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
