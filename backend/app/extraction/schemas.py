"""Pydantic contracts for LLM output. Values stay strings here: parsing them is the normalizers' job."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FieldOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str | None = None
    evidence: str | None = None
    page: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_values(cls, data: Any) -> Any:
        # Some models return "invoice_number": "INV-1" instead of an object. Keep the value but it will have
        # no evidence, so the evidence check rejects it rather than trusting it.
        if data is None:
            return {}
        if not isinstance(data, dict):
            return {"value": data}
        return data

    @field_validator("value", "evidence", mode="before")
    @classmethod
    def _to_clean_str(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, bool) or isinstance(v, (list, dict)):
            raise ValueError("expected a string or number")
        s = str(v).strip()
        return s or None

    @field_validator("page", mode="before")
    @classmethod
    def _page_int(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None  # a bad page hint is not fatal; the evidence is searched on every page


class LineItemOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: FieldOut = Field(default_factory=FieldOut)
    quantity: FieldOut = Field(default_factory=FieldOut)
    unit_price: FieldOut = Field(default_factory=FieldOut)
    tax: FieldOut = Field(default_factory=FieldOut)
    total: FieldOut = Field(default_factory=FieldOut)


class InvoiceOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoice_number: FieldOut = Field(default_factory=FieldOut)
    invoice_date: FieldOut = Field(default_factory=FieldOut)
    supplier_name: FieldOut = Field(default_factory=FieldOut)
    customer_name: FieldOut = Field(default_factory=FieldOut)
    subtotal: FieldOut = Field(default_factory=FieldOut)
    tax: FieldOut = Field(default_factory=FieldOut)
    total: FieldOut = Field(default_factory=FieldOut)
    currency: FieldOut = Field(default_factory=FieldOut)
    line_items: list[LineItemOut] = Field(default_factory=list)

    @field_validator("line_items", mode="before")
    @classmethod
    def _none_is_empty(cls, v: Any) -> Any:
        return [] if v is None else v


class ExtractionOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    invoices: list[InvoiceOut] = Field(default_factory=list)

    @field_validator("invoices", mode="before")
    @classmethod
    def _none_is_empty(cls, v: Any) -> Any:
        return [] if v is None else v


HEADER_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "total",
                 "currency")
LINE_FIELDS = ("description", "quantity", "unit_price", "tax", "total")
MONEY_FIELDS = frozenset({"subtotal", "tax", "total", "unit_price"})
NUMBER_FIELDS = MONEY_FIELDS | {"quantity"}
