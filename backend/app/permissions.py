"""Role-based access control. The server enforces these; the UI only hides what a role can't use."""
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "Doctor": frozenset({"requisition:create"}),
    "Nurse": frozenset({"requisition:create", "issuance:handle"}),
    "Technician": frozenset({"issuance:handle"}),
    "Blood Bank Admin": frozenset({"requisition:create", "issuance:handle", "audit:read"}),
}


def permissions_for(role: str) -> list[str]:
    return sorted(ROLE_PERMISSIONS.get(role, frozenset()))
