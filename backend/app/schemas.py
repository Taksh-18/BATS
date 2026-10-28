"""Pydantic v2 schemas. Read models never expose password_hash."""
from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class BloodType(str, Enum):
    A_POS = "A+"
    A_NEG = "A-"
    B_POS = "B+"
    B_NEG = "B-"
    AB_POS = "AB+"
    AB_NEG = "AB-"
    O_POS = "O+"
    O_NEG = "O-"


class UnitStatus(str, Enum):
    AVAILABLE = "Available"
    RESERVED = "Reserved"
    ISSUED = "Issued"
    QUARANTINED = "Quarantined"
    EXPIRED = "Expired"
    TRANSFUSED = "Transfused"


class Urgency(str, Enum):
    ROUTINE = "Routine"
    URGENT = "Urgent"
    CRITICAL = "Critical"


class RequisitionStatus(str, Enum):
    PENDING = "Pending"
    PARTIAL = "Partially Fulfilled"
    FULFILLED = "Fulfilled"
    CANCELLED = "Cancelled"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Hospital / Blood bank -------------------------------------------------
class HospitalCreate(BaseModel):
    name: str = Field(max_length=150)
    address: str
    contact_number: str = Field(max_length=20)


class HospitalRead(ORMModel, HospitalCreate):
    hospital_id: int
    hms_sync_status: str


class BloodBankCreate(BaseModel):
    name: str = Field(max_length=150)
    location: str
    contact_number: str = Field(max_length=20)


class BloodBankRead(ORMModel, BloodBankCreate):
    blood_bank_id: int


# --- Staff -----------------------------------------------------------------
class StaffCreate(BaseModel):
    full_name: str = Field(max_length=120)
    role: str = Field(max_length=50)
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8)  # hashed in Phase 4
    hospital_id: int


class StaffRead(ORMModel):
    staff_id: int
    full_name: str
    role: str
    username: str
    hospital_id: int


# --- Donor / Donation ------------------------------------------------------
class DonorCreate(BaseModel):
    full_name: str = Field(max_length=120)
    blood_type: BloodType
    contact_number: str = Field(max_length=20)
    email: EmailStr | None = None
    address: str | None = None
    date_of_birth: date


class DonorRead(ORMModel, DonorCreate):
    donor_id: int
    eligibility_status: str
    last_donation_date: date | None


class DonationCreate(BaseModel):
    donor_id: int
    blood_bank_id: int
    volume_ml: int = Field(ge=100, le=600)


class DonationRead(ORMModel, DonationCreate):
    donation_id: int
    donation_date: date


# --- Blood unit ------------------------------------------------------------
class BloodUnitRead(ORMModel):
    unit_id: int
    donation_id: int
    blood_type: BloodType
    component: str
    batch_number: str
    collection_date: date
    expiry_date: date
    status: UnitStatus


# --- Requisition -----------------------------------------------------------
class RequisitionCreate(BaseModel):
    blood_type_requested: BloodType
    quantity: int = Field(gt=0)
    urgency_level: Urgency = Urgency.ROUTINE


class RequisitionRead(ORMModel, RequisitionCreate):
    requisition_id: int
    hospital_id: int
    staff_id: int
    status: RequisitionStatus
    requisition_date: datetime


# --- Issuance (endpoint logic arrives in Phase 2) --------------------------
class IssueRead(ORMModel):
    issue_id: int
    requisition_id: int
    unit_id: int
    issued_by_staff_id: int
    verified_by_staff_id: int
    issue_date: datetime


class IssueRequest(BaseModel):
    """The issuer is whoever holds the JWT; the verifier proves identity with their own credentials."""
    unit_id: int
    requisition_id: int
    verifier_username: str = Field(min_length=1, max_length=50)
    verifier_password: str = Field(min_length=1, max_length=128)
    verifier_confirmed: bool


class IssueResponse(IssueRead):
    requisition_status: RequisitionStatus
    hms_synced: bool


# --- Mock HMS --------------------------------------------------------------
class HMSIssueEvent(BaseModel):
    event: str = "blood_unit.issued"
    issue_id: int
    unit_id: int
    requisition_id: int
    hospital_id: int
    blood_type: BloodType
    issued_by_staff_id: int
    verified_by_staff_id: int
    issued_at: datetime


# --- Auth ------------------------------------------------------------------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    staff: StaffRead


# --- List views ------------------------------------------------------------
class RequisitionListItem(RequisitionRead):
    staff_name: str
    issued_count: int


class IssueListItem(BaseModel):
    issue_id: int
    unit_id: int
    requisition_id: int
    blood_type: BloodType
    issuer_username: str
    issuer_name: str
    verifier_username: str
    verifier_name: str
    issue_date: datetime


class AuditRead(ORMModel):
    audit_id: int
    created_at: datetime
    actor_username: str
    action: str
    details: str
    hash: str


class ChainStatus(BaseModel):
    valid: bool
    checked: int
    first_bad_audit_id: int | None
    reason: str | None = None


class StatsRead(BaseModel):
    total_units: int
    available_units: int
    active_requisitions: int
    critical_requisitions: int
    issuances: int
    expiring_soon: int
    available_by_type: dict[str, int]
    hms_pending: int
    audit_chain_valid: bool | None  # None until the first background check has run


class HMSStatus(BaseModel):
    online: bool
    endpoint: str


class UnitPage(BaseModel):
    items: list[BloodUnitRead]
    total: int
    limit: int
    offset: int


class HMSInboundEvent(BaseModel):
    """An update the hospital HMS sends back to BATS."""
    event_ref: str = Field(min_length=1, max_length=100)  # unique per event; makes replays harmless
    action: Literal["unit_transfused", "unit_returned"]
    unit_id: int
    cold_chain_ok: bool = True
