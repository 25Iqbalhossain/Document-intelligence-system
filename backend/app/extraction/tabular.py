"""Deterministic mapping of spreadsheet/CSV tables to invoices (no LLM involved).

Column headers are matched against synonym lists. Rows that share an invoice number become one invoice whose
rows are line items (when item/quantity/price columns exist); otherwise each row is one invoice.
Every value cites its cell, so spreadsheet-derived data is as traceable as document-derived data.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any

from app.extraction.extractor import FieldResult, InvoiceResult, LineResult
from app.extraction.normalizers import (
    clean_invoice_number,
    invoice_key,
    normalize_currency,
    parse_date,
    parse_money,
)
from app.extraction.schemas import HEADER_FIELDS, LINE_FIELDS, MONEY_FIELDS

# field -> synonyms (normalised: lower-case alphanumerics), in priority order
HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "invoice_number": ("invoiceno", "invoicenumber", "invno", "invoiceid", "billno", "billnumber", "invoice", "inv",
                       "ইনভয়েসনং", "ইনভয়েসনম্বর"),
    "invoice_date": ("invoicedate", "billdate", "invdate", "date", "তারিখ"),
    "supplier_name": ("suppliername", "supplier", "vendorname", "vendor", "seller", "party", "সরবরাহকারী"),
    "customer_name": ("customername", "customer", "buyer", "client", "billto", "ক্রেতা"),
    "subtotal": ("subtotal", "netamount", "taxableamount", "amountexcltax", "basicamount", "net"),
    "tax": ("taxamount", "vatamount", "tax", "vat", "gst"),
    "total": ("grandtotal", "invoicetotal", "totalamount", "total", "grossamount", "gross", "payable", "amount",
              "মোট"),
    "currency": ("currency", "ccy", "curr", "মুদ্রা"),
}
LINE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "description": ("description", "itemname", "item", "product", "particulars", "বিবরণ"),
    "quantity": ("quantity", "qty", "পরিমাণ"),
    "unit_price": ("unitprice", "rate", "price", "unitrate"),
    "tax": ("linetax", "itemtax"),
    "total": ("linetotal", "itemtotal", "lineamount"),
}


def _h(header: str) -> str:
    return re.sub(r"[\W_]+", "", str(header).casefold())


def map_columns(headers: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    """(header_field -> column index, line_field -> column index). A column is used for one field only."""
    normed = [_h(h) for h in headers]
    used: set[int] = set()

    def pick(table: dict[str, tuple[str, ...]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for fld, syns in table.items():
            for syn in syns:
                idx = next((i for i, n in enumerate(normed) if n == syn and i not in used), None)
                if idx is not None:
                    out[fld] = idx
                    used.add(idx)
                    break
        return out

    lines = pick(LINE_SYNONYMS)  # line columns first: 'Line Total' must not be mistaken for the invoice total
    return pick(HEADER_SYNONYMS), lines


def _cell_ref(col: int, row: int) -> str:
    name = ""
    n = col + 1
    while n:
        n, r = divmod(n - 1, 26)
        name = chr(65 + r) + name
    return f"{name}{row}"


def _text(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00", s):  # Excel date-times come through as ISO strings
        s = s[:10]
    return s or None


def _cell(name: str, raw: Any, sheet: str, page: int, col: int, row: int) -> FieldResult:
    text = _text(raw)
    if text is None:
        return FieldResult(name, None, None, status="missing")
    ref = f"{sheet}!{_cell_ref(col, row)}"
    res = FieldResult(name, text, None, evidence=text, page=page, matched_text=ref)
    warnings: list[str] = []
    if name in MONEY_FIELDS or name == "quantity":
        m = parse_money(text)
        value, warnings = m.value, list(m.warnings)
    elif name == "invoice_date":
        d = parse_date(text)
        value, warnings = d.value, list(d.warnings)
    elif name == "currency":
        value = normalize_currency(text) or (text.upper() if re.fullmatch(r"[A-Za-z]{3}", text) else None)
    elif name == "invoice_number":
        value = clean_invoice_number(text)
    else:
        value = re.sub(r"\s+", " ", text)
    if value is None:
        res.status, res.reason, res.confidence = "unparseable", "unparseable_value", 0.0
        return res
    res.value, res.status, res.warnings = value, "verified", warnings
    res.confidence = 0.85 if warnings else 1.0
    res.components = {"evidence": 1.0, "ocr": 1.0, "parse": 0.85 if warnings else 1.0}
    return res


def map_table(table: dict[str, Any], page: int) -> tuple[list[InvoiceResult], str | None]:
    """Returns (invoices, skip_reason). skip_reason is set when the sheet has no recognisable invoice columns."""
    headers, rows = table.get("headers") or [], table.get("rows") or []
    sheet = table.get("sheet") or "csv"
    numbers = table.get("row_numbers") or list(range(2, len(rows) + 2))
    hmap, lmap = map_columns(headers)
    if "invoice_number" not in hmap or not ({"total", "subtotal"} & hmap.keys()):
        return [], f"no invoice columns recognised (headers: {headers[:12]})"

    has_lines = bool({"quantity", "unit_price", "description"} & lmap.keys())
    groups: OrderedDict[Any, list[int]] = OrderedDict()
    for i, row in enumerate(rows):
        num = _text(row[hmap["invoice_number"]]) if hmap["invoice_number"] < len(row) else None
        key = (invoice_key(num) if (has_lines and num) else f"row{i}")
        groups.setdefault(key, []).append(i)

    invoices: list[InvoiceResult] = []
    for idxs in groups.values():
        def val(field_idx: int, i: int) -> Any:
            return rows[i][field_idx] if field_idx < len(rows[i]) else None

        fields: dict[str, FieldResult] = {}
        for name in HEADER_FIELDS:
            col = hmap.get(name)
            if col is None:
                fields[name] = FieldResult(name, None, None, status="missing")
                continue
            # header-level value: first row of the group that has one
            pick = next((i for i in idxs if _text(val(col, i)) is not None), idxs[0])
            fields[name] = _cell(name, val(col, pick), sheet, page, col, numbers[pick])
        lines: list[LineResult] = []
        if has_lines:
            for i in idxs:
                lf = {n: (_cell(n, val(lmap[n], i), sheet, page, lmap[n], numbers[i]) if n in lmap
                          else FieldResult(n, None, None, status="missing")) for n in LINE_FIELDS}
                if any(f.status != "missing" for f in lf.values()):
                    lines.append(LineResult(len(lines) + 1, lf))
        reasons = [f"field_unparseable:{n}" for n, r in fields.items() if r.status == "unparseable"]
        for li in lines:
            reasons += [f"field_unparseable:line_items[{li.line_no}].{n}" for n, r in li.fields.items()
                        if r.status == "unparseable"]
        first_row = numbers[idxs[0]]
        invoices.append(InvoiceResult(
            fields, lines, {"sheet": sheet, "rows": [numbers[i] for i in idxs]}, (page, page),
            list(dict.fromkeys(reasons)), set(), fields["invoice_date"].raw is not None
            and parse_date(fields["invoice_date"].raw).ambiguous,
        ))
        invoices[-1].raw["row_ref"] = f"{sheet}!{first_row}"
    return invoices, None
