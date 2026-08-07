"""Erori de domeniu.

Fiecare eroare purtează un `ValidationIssue` structurat, nu un mesaj liber: agentul
vocal îl *povestește* clientului, dar nu îl inventează și nu îl reinterpretează.
"""

from __future__ import annotations

from .models import ValidationIssue


class DomainError(Exception):
    """Refuz motivat al domeniului. Niciodată folosit pentru bug-uri de programare."""

    def __init__(self, issue: ValidationIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue

    @classmethod
    def of(cls, code: str, message: str, field: str | None = None) -> DomainError:
        return cls(ValidationIssue(code=code, message=message, field=field))
