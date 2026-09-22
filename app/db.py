"""
db.py - Database table for reimbursement claims (using SQLAlchemy).

One row = one bill that an employee submitted.
"""
import json
import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings

DATABASE_URL = settings.database_url

# SQLite needs its folder to exist (e.g. /home/data on Azure)
if DATABASE_URL.startswith("sqlite:///"):
    db_path = DATABASE_URL.replace("sqlite:///", "", 1)
    folder = os.path.dirname(db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_name: Mapped[str] = mapped_column(String(120), index=True)

    # What the AI read from the bill
    vendor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bill_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    items: Mapped[str] = mapped_column(Text, default="[]")           # JSON list
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # What the AI decided
    category: Mapped[str] = mapped_column(String(40))
    ai_reason: Mapped[str] = mapped_column(Text, default="")
    flags: Mapped[str] = mapped_column(Text, default="[]")           # JSON list

    # File info
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    blob_name: Mapped[str] = mapped_column(String(200))
    content_type: Mapped[str] = mapped_column(String(60))

    # HR workflow
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/approved/rejected
    hr_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_name": self.employee_name,
            "vendor": self.vendor,
            "bill_date": self.bill_date,
            "amount": self.amount,
            "currency": self.currency,
            "items": json.loads(self.items or "[]"),
            "ocr_confidence": self.ocr_confidence,
            "category": self.category,
            "ai_reason": self.ai_reason,
            "flags": json.loads(self.flags or "[]"),
            "file_hash": self.file_hash,
            "content_type": self.content_type,
            "status": self.status,
            "hr_comment": self.hr_comment,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RebalanceRequest(Base):
    """
    One row = an employee asking to move unused limit between two categories
    for one month, e.g. "move Rs 1,000 from Books & Learning to Telecom".
    We never edit the default limits; approved rows are added on top of them,
    so there is a full history of every change (an audit trail).
    """
    __tablename__ = "rebalance_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_name: Mapped[str] = mapped_column(String(120), index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM
    from_category: Mapped[str] = mapped_column(String(40))
    to_category: Mapped[str] = mapped_column(String(40))
    amount: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/approved/rejected
    hr_comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "employee_name": self.employee_name,
            "month": self.month,
            "from_category": self.from_category,
            "to_category": self.to_category,
            "amount": self.amount,
            "reason": self.reason,
            "status": self.status,
            "hr_comment": self.hr_comment,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def init_db():
    """Create tables if they don't exist yet."""
    try:
        Base.metadata.create_all(engine)
    except OperationalError:
        # Two workers starting together may race; the other one created it.
        pass


def get_db():
    """FastAPI dependency: gives each request its own DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
