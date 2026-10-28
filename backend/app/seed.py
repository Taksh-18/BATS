"""Demo data for local development. Run after sql/schema.sql:  python -m app.seed

Both demo accounts share one well-known password, so never run this against a real deployment.
"""
import asyncio
import hashlib
from datetime import date, timedelta

from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal, engine
from app.models import BloodBank, BloodRequisition, BloodUnit, Donation, Donor, Hospital, HospitalStaff
from app.security import hash_password
from app.timeutil import today as day_today
from app.services import audit

DEMO_PASSWORD = "Bats@123"

# (blood_type, component, collected N days ago, expires in N days, status)
UNITS = [
    ("O+",  "Whole Blood", 10, 25, "Available"),
    ("A+",  "Packed RBC",  9,  33, "Available"),
    ("A+",  "Whole Blood", 4,  31, "Available"),
    ("B+",  "Platelets",   3,  2,  "Quarantined"),
    ("AB+", "Plasma",      40, 5,  "Quarantined"),
    ("O-",  "Whole Blood", 6,  29, "Available"),
    ("A-",  "Packed RBC",  7,  28, "Available"),
    ("B-",  "Plasma",      5,  30, "Available"),
    ("O+",  "Platelets",   2,  5,  "Available"),
    ("O-",  "Whole Blood", 50, -3, "Available"),  # expired: used to demo the expiry rule
]

REQUISITIONS = [("O+", 2, "Critical"), ("A+", 1, "Urgent"), ("B-", 3, "Routine")]


async def main() -> None:
    today = day_today()
    async with SessionLocal() as db:
        if (await db.execute(select(Hospital.hospital_id).limit(1))).first():
            print("Database already has data; refusing to seed twice.")
            return

        hospital = Hospital(name="Manipal Teaching Hospital", address="Dehmi Kalan, Jaipur",
                            contact_number="0141-3999000", hms_sync_status="Connected",
                            hms_key_hash=hashlib.sha256(settings.hms_api_key.encode()).hexdigest())
        bank = BloodBank(name="MUJ Central Blood Bank", location="Jaipur-Ajmer Expressway",
                         contact_number="0141-3999100")
        db.add_all([hospital, bank])
        await db.flush()

        pw = hash_password(DEMO_PASSWORD)
        meera = HospitalStaff(full_name="Meera Sharma", role="Nurse", username="staff_meera",
                              password_hash=pw, hospital_id=hospital.hospital_id)
        rohit = HospitalStaff(full_name="Rohit Mehta", role="Technician", username="staff_rohit",
                              password_hash=pw, hospital_id=hospital.hospital_id)
        admin = HospitalStaff(full_name="Anita Rao", role="Blood Bank Admin", username="staff_admin",
                              password_hash=pw, hospital_id=hospital.hospital_id)
        donor = Donor(full_name="Rahul Singh", blood_type="A+", contact_number="9800000001",
                      email="rahul@example.com", date_of_birth=date(1995, 4, 12))
        db.add_all([meera, rohit, admin, donor])
        await db.flush()

        for i, (blood_type, component, age, shelf, status) in enumerate(UNITS, start=1):
            donation = Donation(donor_id=donor.donor_id, blood_bank_id=bank.blood_bank_id,
                                donation_date=today - timedelta(days=age), volume_ml=450)
            db.add(donation)
            await db.flush()
            db.add(BloodUnit(donation_id=donation.donation_id, blood_type=blood_type, component=component,
                             batch_number=f"BN-{today:%y%m}-{i:03d}",
                             collection_date=today - timedelta(days=age),
                             expiry_date=today + timedelta(days=shelf), status=status))

        for blood_type, qty, urgency in REQUISITIONS:
            db.add(BloodRequisition(hospital_id=hospital.hospital_id, staff_id=meera.staff_id,
                                    blood_type_requested=blood_type, quantity=qty, urgency_level=urgency))

        await audit.record(db, None, "system", "SYSTEM_SEED", "demo data loaded")
        await db.commit()
        print(f"Seeded. Demo logins: staff_meera (Nurse), staff_rohit (Technician), staff_admin (Blood Bank Admin); password {DEMO_PASSWORD}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
