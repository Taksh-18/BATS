-- BATS schema (PostgreSQL 14+), derived from the ER diagram in BATS_Project_Context.md
-- Run: psql -U bats -d bats -f sql/schema.sql

BEGIN;

CREATE TABLE hospital (
    hospital_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            VARCHAR(150) NOT NULL,
    address         TEXT         NOT NULL,
    contact_number  VARCHAR(20)  NOT NULL,
    hms_sync_status VARCHAR(20)  NOT NULL DEFAULT 'Disconnected'
        CHECK (hms_sync_status IN ('Connected', 'Disconnected', 'Error')),
    hms_key_hash    CHAR(64) UNIQUE   -- SHA-256 of this hospital's HMS API key; NULL = no HMS integration
);

CREATE TABLE blood_bank (
    blood_bank_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            VARCHAR(150) NOT NULL,
    location        TEXT         NOT NULL,
    contact_number  VARCHAR(20)  NOT NULL
);

CREATE TABLE admin (
    admin_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name       VARCHAR(120) NOT NULL,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL
);

CREATE TABLE hospital_staff (
    staff_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name       VARCHAR(120) NOT NULL,
    role            VARCHAR(50)  NOT NULL
        CHECK (role IN ('Doctor', 'Nurse', 'Technician', 'Blood Bank Admin')),
    username        VARCHAR(50)  NOT NULL UNIQUE,
    password_hash   VARCHAR(255) NOT NULL,
    hospital_id     BIGINT       NOT NULL REFERENCES hospital (hospital_id)
);

CREATE TABLE donor (
    donor_id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name           VARCHAR(120) NOT NULL,
    blood_type          VARCHAR(3)   NOT NULL
        CHECK (blood_type IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')),
    contact_number      VARCHAR(20)  NOT NULL,
    email               VARCHAR(255) UNIQUE,
    address             TEXT,
    date_of_birth       DATE         NOT NULL,
    eligibility_status  VARCHAR(20)  NOT NULL DEFAULT 'Eligible'
        CHECK (eligibility_status IN ('Eligible', 'Deferred', 'Ineligible')),
    last_donation_date  DATE
);

CREATE TABLE donation (
    donation_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    donor_id        BIGINT NOT NULL REFERENCES donor (donor_id),
    blood_bank_id   BIGINT NOT NULL REFERENCES blood_bank (blood_bank_id),
    donation_date   DATE   NOT NULL DEFAULT CURRENT_DATE,
    volume_ml       INT    NOT NULL CHECK (volume_ml BETWEEN 100 AND 600)
);

CREATE TABLE blood_unit (
    unit_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    donation_id     BIGINT NOT NULL REFERENCES donation (donation_id),
    blood_type      VARCHAR(3) NOT NULL
        CHECK (blood_type IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')),
    component       VARCHAR(20) NOT NULL DEFAULT 'Whole Blood'
        CHECK (component IN ('Whole Blood', 'Plasma', 'Platelets', 'Packed RBC')),
    batch_number    VARCHAR(30) NOT NULL DEFAULT 'UNBATCHED',
    collection_date DATE   NOT NULL,
    expiry_date     DATE   NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'Available'
        CHECK (status IN ('Available', 'Reserved', 'Issued', 'Quarantined', 'Expired', 'Transfused')),
    CHECK (expiry_date > collection_date)
);

CREATE TABLE blood_requisition (
    requisition_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hospital_id         BIGINT NOT NULL REFERENCES hospital (hospital_id),
    staff_id            BIGINT NOT NULL REFERENCES hospital_staff (staff_id),
    blood_type_requested VARCHAR(3) NOT NULL
        CHECK (blood_type_requested IN ('A+','A-','B+','B-','AB+','AB-','O+','O-')),
    quantity            INT    NOT NULL CHECK (quantity > 0),
    urgency_level       VARCHAR(10) NOT NULL DEFAULT 'Routine'
        CHECK (urgency_level IN ('Routine', 'Urgent', 'Critical')),
    status              VARCHAR(20) NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Partially Fulfilled', 'Fulfilled', 'Cancelled')),
    requisition_date    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Two-person verification: both FKs point at hospital_staff, and the CHECK
-- guarantees they are different people. UNIQUE (unit_id) means a unit can be
-- issued only once, which also blocks a double-issue race at the DB level.
CREATE TABLE blood_issue (
    issue_id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    requisition_id         BIGINT NOT NULL REFERENCES blood_requisition (requisition_id),
    unit_id                BIGINT NOT NULL UNIQUE REFERENCES blood_unit (unit_id),
    issued_by_staff_id     BIGINT NOT NULL REFERENCES hospital_staff (staff_id),
    verified_by_staff_id   BIGINT NOT NULL REFERENCES hospital_staff (staff_id),
    issue_date             TIMESTAMPTZ NOT NULL DEFAULT now(),
    returned_at            TIMESTAMPTZ,  -- set when the hospital returns the unit unused
    CONSTRAINT chk_two_person_rule
        CHECK (issued_by_staff_id <> verified_by_staff_id)
);

-- Two-person issuance: one staff member REQUESTS, a different one APPROVES from their own
-- sign-in. The unit is only issued (and a blood_issue row created) on approval.
CREATE TABLE issuance_request (
    request_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unit_id                BIGINT NOT NULL REFERENCES blood_unit (unit_id),
    requisition_id         BIGINT NOT NULL REFERENCES blood_requisition (requisition_id),
    requested_by_staff_id  BIGINT NOT NULL REFERENCES hospital_staff (staff_id),
    status                 VARCHAR(10) NOT NULL DEFAULT 'Pending'
        CHECK (status IN ('Pending', 'Approved', 'Declined', 'Cancelled', 'Expired', 'Failed')),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at             TIMESTAMPTZ NOT NULL,
    decided_by_staff_id    BIGINT REFERENCES hospital_staff (staff_id),
    decided_at             TIMESTAMPTZ,
    failure_rule           VARCHAR(40)
);
CREATE UNIQUE INDEX uq_one_pending_request_per_unit ON issuance_request (unit_id) WHERE status = 'Pending';

-- Idempotency log for updates the hospital HMS sends back: replaying an event is a no-op.
CREATE TABLE hms_inbound (
    inbound_key  TEXT PRIMARY KEY,
    unit_id      BIGINT NOT NULL REFERENCES blood_unit (unit_id),
    action       VARCHAR(20) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- SIMULATION ONLY: the placeholder HMS's own inbox. A real hospital system would keep this
-- on its side; it lives here so the demo survives restarts. Not referenced by BATS tables.
CREATE TABLE mock_hms_event (
    event_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id       BIGINT NOT NULL UNIQUE,
    hms_reference  VARCHAR(30) NOT NULL,
    payload        JSONB NOT NULL,
    state          VARCHAR(12) NOT NULL DEFAULT 'Received' CHECK (state IN ('Received', 'Transfused', 'Returned')),
    received_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at    TIMESTAMPTZ
);

-- Transactional outbox: the HMS event is stored in the same transaction as the issuance,
-- so a crash or HMS outage can never lose it; a background worker retries until delivered.
CREATE TABLE hms_outbox (
    outbox_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id         BIGINT NOT NULL UNIQUE REFERENCES blood_issue (issue_id),
    hospital_id      BIGINT NOT NULL REFERENCES hospital (hospital_id),
    payload          JSONB  NOT NULL,
    status           VARCHAR(10) NOT NULL DEFAULT 'Pending' CHECK (status IN ('Pending', 'Sent')),
    attempts         INT    NOT NULL DEFAULT 0,
    last_error       TEXT,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at          TIMESTAMPTZ
);
CREATE INDEX idx_outbox_pending ON hms_outbox (next_attempt_at) WHERE status = 'Pending';

-- Tamper-evident audit trail: each row stores SHA-256(prev_hash + its own fields),
-- so editing or removing any row breaks every hash after it. The trigger blocks
-- UPDATE/DELETE; the hash chain still exposes tampering by someone with owner rights.
CREATE TABLE audit_log (
    audit_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TIMESTAMPTZ  NOT NULL,
    actor_staff_id  BIGINT REFERENCES hospital_staff (staff_id),
    actor_username  VARCHAR(50)  NOT NULL,
    action          VARCHAR(50)  NOT NULL,
    details         TEXT         NOT NULL,
    prev_hash       CHAR(64)     NOT NULL,
    hash            CHAR(64)     NOT NULL UNIQUE
);

CREATE FUNCTION audit_log_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_log_append_only
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();

CREATE INDEX idx_unit_search     ON blood_unit (blood_type, status, expiry_date);
CREATE INDEX idx_requisition_hosp ON blood_requisition (hospital_id, status);
CREATE INDEX idx_issue_requisition ON blood_issue (requisition_id);
CREATE INDEX idx_staff_hospital   ON hospital_staff (hospital_id);

COMMIT;
