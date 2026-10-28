"""Which donor blood groups may be given to a patient whose requisition asks for `requested`.

Exact matches are always fine. Substitution is only offered for Critical requisitions, and only
for components where ABO/Rh substitution is routine practice (red cells, plasma). Platelets and
whole blood are matched exactly. This is a teaching model, not clinical guidance.
"""
RBC_ACCEPTS = {  # recipient -> donor groups whose red cells are compatible
    "A+": {"A+", "A-", "O+", "O-"}, "A-": {"A-", "O-"},
    "B+": {"B+", "B-", "O+", "O-"}, "B-": {"B-", "O-"},
    "AB+": {"A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}, "AB-": {"A-", "B-", "AB-", "O-"},
    "O+": {"O+", "O-"}, "O-": {"O-"},
}
PLASMA_DONOR_ABO = {"A": {"A", "AB"}, "B": {"B", "AB"}, "AB": {"AB"}, "O": {"A", "B", "AB", "O"}}


def acceptable_types(requested: str, component: str, urgency: str) -> set[str]:
    if urgency != "Critical":
        return {requested}
    if component == "Packed RBC":
        return RBC_ACCEPTS[requested]
    if component == "Plasma":
        abo = requested.rstrip("+-")
        return {t for t in RBC_ACCEPTS if t.rstrip("+-") in PLASMA_DONOR_ABO[abo]}
    return {requested}
