"""SQLAlchemy ORM models. The schema itself lives in sql/schema.sql (source of truth)."""
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Hospital(Base):
    __tablename__ = "hospital"

    hospital_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str] = mapped_column(Text)
    contact_number: Mapped[str] = mapped_column(String(20))
    hms_sync_status: Mapped[str] = mapped_column(String(20), default="Disconnected")


class BloodBank(Base):
    __tablename__ = "blood_bank"

    blood_bank_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    location: Mapped[str] = mapped_column(Text)
    contact_number: Mapped[str] = mapped_column(String(20))


class Admin(Base):
    __tablename__ = "admin"

    admin_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))


class HospitalStaff(Base):
    __tablename__ = "hospital_staff"

    staff_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(50))
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospital.hospital_id"))


class Donor(Base):
    __tablename__ = "donor"

    donor_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    blood_type: Mapped[str] = mapped_column(String(3))
    contact_number: Mapped[str] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    address: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[date] = mapped_column(Date)
    eligibility_status: Mapped[str] = mapped_column(String(20), default="Eligible")
    last_donation_date: Mapped[date | None] = mapped_column(Date)


class Donation(Base):
    __tablename__ = "donation"

    donation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    donor_id: Mapped[int] = mapped_column(ForeignKey("donor.donor_id"))
    blood_bank_id: Mapped[int] = mapped_column(ForeignKey("blood_bank.blood_bank_id"))
    donation_date: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    volume_ml: Mapped[int] = mapped_column(Integer)


class BloodUnit(Base):
    __tablename__ = "blood_unit"

    unit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    donation_id: Mapped[int] = mapped_column(ForeignKey("donation.donation_id"))
    blood_type: Mapped[str] = mapped_column(String(3))
    component: Mapped[str] = mapped_column(String(20), default="Whole Blood")
    batch_number: Mapped[str] = mapped_column(String(30), default="UNBATCHED")
    collection_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="Available")


class BloodRequisition(Base):
    __tablename__ = "blood_requisition"

    requisition_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospital.hospital_id"))
    staff_id: Mapped[int] = mapped_column(ForeignKey("hospital_staff.staff_id"))
    blood_type_requested: Mapped[str] = mapped_column(String(3))
    quantity: Mapped[int] = mapped_column(Integer)
    urgency_level: Mapped[str] = mapped_column(String(10), default="Routine")
    status: Mapped[str] = mapped_column(String(20), default="Pending")
    requisition_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BloodIssue(Base):
    __tablename__ = "blood_issue"
    __table_args__ = (
        CheckConstraint("issued_by_staff_id <> verified_by_staff_id", name="chk_two_person_rule"),
    )

    issue_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    requisition_id: Mapped[int] = mapped_column(ForeignKey("blood_requisition.requisition_id"))
    unit_id: Mapped[int] = mapped_column(ForeignKey("blood_unit.unit_id"), unique=True)
    issued_by_staff_id: Mapped[int] = mapped_column(ForeignKey("hospital_staff.staff_id"))
    verified_by_staff_id: Mapped[int] = mapped_column(ForeignKey("hospital_staff.staff_id"))
    issue_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_staff_id: Mapped[int | None] = mapped_column(ForeignKey("hospital_staff.staff_id"))
    actor_username: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))
    details: Mapped[str] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class HmsOutbox(Base):
    __tablename__ = "hms_outbox"

    outbox_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("blood_issue.issue_id"), unique=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospital.hospital_id"))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10), default="Pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HmsInbound(Base):
    __tablename__ = "hms_inbound"

    inbound_key: Mapped[str] = mapped_column(Text, primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("blood_unit.unit_id"))
    action: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MockHmsEvent(Base):
    """Simulation only: the placeholder HMS's inbox."""
    __tablename__ = "mock_hms_event"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    hms_reference: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(12), default="Received")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
