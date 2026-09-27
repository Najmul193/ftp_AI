"""The organisation's identifying names, as vault entities.

Registered in a vault before masking text a person typed, so "why did Dhaka
Main fall?" leaves as "why did BR_7KQ fall?". A field the policy lets pass in
clear is not registered, so it is neither masked nor treated as a leak.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gateway.policy import FieldPolicy
from app.ai.gateway.tokenizer import Entity, Vault
from app.models import Branch, District, Division


def org_entities(db: Session, policy: FieldPolicy) -> tuple[list[Entity], set[str]]:
    """(entities to register, numeric codes for the DLP)."""
    ents: list[Entity] = []
    codes: set[str] = set()
    if policy.action("branch") != "pass":
        for b in db.scalars(select(Branch)):
            # Names repeat ("Dhaka Main" is two branches), so the display
            # carries the code and a person can tell them apart.
            ents.append(Entity("BR", b.branch_code, f"{b.branch_name} ({b.branch_code})",
                               (b.branch_name, b.branch_code)))
            if b.branch_code.isdigit():
                codes.add(b.branch_code)
    if policy.action("district") != "pass":
        for d in db.scalars(select(District)):
            ents.append(Entity("DIST", d.code, d.name, (d.name,)))
    if policy.action("division") != "pass":
        for v in db.scalars(select(Division)):
            ents.append(Entity("DIV", v.code, f"{v.name} division", (v.name,)))
    return ents, codes


def user_vault(db: Session, policy: FieldPolicy) -> tuple[Vault, set[str]]:
    v = Vault()
    ents, codes = org_entities(db, policy)
    v.register(ents)
    return v, codes
