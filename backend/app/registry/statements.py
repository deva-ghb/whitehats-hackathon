"""A proved registry build is WhiteHats speaking as the supplier of that build: its advisories do not apply to it.

A dependency finding on exactly the build's package and version, for one of the advisories the build fixed, is
marked not affected (SCAN.5): still reported, never escalated, with the proof in words. Any other version, or any
other advisory on the same build, is not covered.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..decisions_api.suspect import NOT_AFFECTED, Suspect
from ..models import RegistryPackage
from .build import normalized


@dataclass(frozen=True)
class RegistryFixed:
    package: str  # "pypdf2@3.0.1+whitehats.1": the name as indexes compare it, as the dependency scanner pins it
    advisories: frozenset[str]
    reason: str
    status: str = NOT_AFFECTED

    def covers(self, suspect: Suspect) -> bool:
        return (suspect.engine == "osv-scanner" and suspect.evidence.package == self.package
                and suspect.rule_id in self.advisories)


def for_company(db: Session, org_id: int) -> list[RegistryFixed]:
    rows = db.scalars(select(RegistryPackage).where(RegistryPackage.org_id == org_id, RegistryPackage.status == "proved"))
    return [RegistryFixed(
        package=f"{normalized(row.name)}@{row.version}", advisories=frozenset(row.advisories),
        reason=(f"Fixed in the WhiteHats Registry build {row.name} {row.version}: {row.proof.get('after', {}).get('said', '')}"
                f" (before: {row.proof.get('before', {}).get('said', '')})")[:600]) for row in rows]
