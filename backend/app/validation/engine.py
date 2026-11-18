"""Runs the rule set. A crashing rule becomes an error finding; it never aborts or vanishes."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date

from app.models.base import Severity
from app.validation.config import ValidationConfig
from app.validation.rules import RULES, Rule, RuleContext
from app.validation.types import ExistingInvoice, Finding, InvoiceData

log = logging.getLogger("docint.validation")
_ORDER = {Severity.error: 0, Severity.warning: 1, Severity.info: 2}


def validate_invoice(
    inv: InvoiceData, config: ValidationConfig | None = None, existing: Iterable[ExistingInvoice] = (),
    today: date | None = None, rules: Sequence[tuple[str, Rule]] = RULES,
) -> list[Finding]:
    cfg = config or ValidationConfig()
    ctx = RuleContext(today=today or date.today(), existing=tuple(existing))
    findings: list[Finding] = []
    for rule_name, rule in rules:
        try:
            findings.extend(rule(inv, cfg, ctx))
        except Exception as exc:  # isolate: one broken rule must not hide the others' results
            log.exception("validation rule %s crashed", rule_name)
            findings.append(Finding("rule_error", Severity.error,
                                    f"Validation rule '{rule_name}' failed to run: {type(exc).__name__}: {exc}"))
    return sorted(findings, key=lambda f: (_ORDER[f.severity], f.rule_id, f.field or ""))


def has_blocking_errors(findings: Iterable[Finding]) -> bool:
    return any(f.severity == Severity.error for f in findings)
