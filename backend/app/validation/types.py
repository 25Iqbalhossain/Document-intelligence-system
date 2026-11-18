"""Pure data types for validation (no ORM dependency, so rules are trivially unit-testable)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.models.base import Severity


@dataclass(frozen=True, slots=True)
class LineData:
    line_no: int
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    refs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class InvoiceData:
    id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    supplier_name: str | None = None
    customer_name: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    currency: str | None = None
    line_items: list[LineData] = field(default_factory=list)
    field_refs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # field -> source references
    detected_currencies: set[str] = field(default_factory=set)  # every currency seen in the source text
    date_ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class ExistingInvoice:
    id: str
    document_id: str | None
    invoice_number: str | None
    supplier_name: str | None
    invoice_date: date | None
    total: Decimal | None


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    severity: Severity
    description: str
    field: str | None = None
    expected: str | None = None
    actual: str | None = None
    # `field` is also an attribute name on this class, so the dataclasses factory is spelled out
    source_references: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    @property
    def key(self) -> tuple[str, str | None, str | None, str | None]:
        """Identity used to keep persisted findings stable across re-runs."""
        return (self.rule_id, self.field, self.expected, self.actual)
