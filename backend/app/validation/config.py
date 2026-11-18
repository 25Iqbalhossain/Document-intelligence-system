"""Validation configuration: tolerances, rounding, required fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.core.config import Settings, get_settings
from app.models.base import Severity


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    tolerance: Decimal = Decimal("0.01")  # absolute difference accepted between expected and actual
    per_term_tolerance: Decimal = Decimal("0")  # extra allowance per summed term (rounded line items)
    decimal_places: int = 2
    rounding: str = ROUND_HALF_UP
    required_fields: tuple[str, ...] = ("invoice_number", "invoice_date", "supplier_name", "total")
    min_date: date = date(1990, 1, 1)
    max_future_days: int = 1
    max_amount: Decimal = Decimal("1000000000000")
    negative_severity: Severity = Severity.warning  # credit notes legitimately carry negative amounts

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> ValidationConfig:
        s = s or get_settings()
        return cls(tolerance=Decimal(s.money_tolerance), decimal_places=s.money_decimal_places)

    def quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.decimal_places)

    def fmt(self, value: Decimal | None) -> str | None:
        """Render a money value for a finding using the configured rounding."""
        if value is None:
            return None
        return str(value.quantize(self.quantum(), rounding=self.rounding))

    def within(self, expected: Decimal, actual: Decimal, terms: int = 1) -> bool:
        return abs(expected - actual) <= self.tolerance + self.per_term_tolerance * terms
