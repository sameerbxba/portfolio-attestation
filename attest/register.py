"""The application register, and the digest that pins an attestation to it.

The digest is the load-bearing idea in this file. An attestation is a statement
about a specific version of a record. Without something that identifies that
version, an attestation silently transfers to whatever the record says later,
which is how a register ends up with a column of green ticks that nobody
actually agreed to.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from .models import Application, Criticality, Owner


def record_digest(app: Application) -> str:
    """A stable fingerprint of the fields the owner is attesting to.

    Sorted keys and a canonical separator so the digest depends on the values
    and not on dictionary ordering or formatting. Truncated to 12 hex
    characters, which is short enough to paste into an email and long enough
    that a collision is not the thing that will go wrong here.
    """
    payload = json.dumps(
        {k: str(v) for k, v in app.attestable().items()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class Register:
    def __init__(self, applications: list[Application], owners: list[Owner]):
        self.applications = {a.id: a for a in applications}
        self.owners = {o.email.lower(): o for o in owners}

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | Path) -> "Register":
        raw = yaml.safe_load(Path(path).read_text())
        owners = [Owner(**o) for o in raw.get("owners", [])]
        apps = []
        for a in raw.get("applications", []):
            a = dict(a)
            a["criticality"] = Criticality(a["criticality"])
            apps.append(Application(**a))
        return cls(apps, owners)

    # ----------------------------------------------------------------- query
    def in_scope(self) -> list[Application]:
        return [a for a in self.applications.values() if a.in_scope]

    def owner_of(self, app: Application) -> Owner | None:
        if not app.owner_email:
            return None
        return self.owners.get(app.owner_email.lower())

    def digest(self, app_id: str) -> str:
        return record_digest(self.applications[app_id])

    # ---------------------------------------------------------------- mutate
    def digest_after(self, app_id: str, **changes) -> str:
        """What the digest would be, without committing the change.

        Exists so that an amendment can be written to the evidence log with
        both the before and after values in it, before the register is
        actually touched. Computing the after value by amending first and
        looking is the version of this that loses the record of an edit when
        the log write fails.
        """
        current = self.applications[app_id]
        return record_digest(Application(**{**current.__dict__, **changes}))

    def amend(self, app_id: str, **changes) -> Application:
        """Change a record.

        Deliberately does not touch any attestation. Whether an existing
        attestation survives an amendment is a control decision, not a
        bookkeeping one, and it belongs in controls.py where it can be tested.
        """
        current = self.applications[app_id]
        updated = Application(**{**current.__dict__, **changes})
        if updated.id != app_id:
            # The id is the key this record is filed under. Changing it here
            # would leave the renamed record stored under the old key and every
            # lookup afterwards inconsistent.
            raise ValueError("an application's id cannot be amended")
        self.applications[app_id] = updated
        return updated
