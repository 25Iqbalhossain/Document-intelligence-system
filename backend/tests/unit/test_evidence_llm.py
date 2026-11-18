import json

import httpx
import pytest
from pydantic import ValidationError

from app.extraction.evidence import locate_evidence, value_supported
from app.extraction.llm import (
    LLMPermanentError,
    LLMTransientError,
    MockProvider,
    OpenAICompatibleProvider,
    get_provider,
)
from app.extraction.prompts import load_prompt
from app.extraction.schemas import ExtractionOut, FieldOut

PAGES = {
    1: "ACME Traders Ltd\nInvoice No: INV-2025-0042\nDate: 15/01/2025\nSubtotal 1,000.00\nVAT 150.00\nTotal ৳ 1,150.00",
    2: "Terms and conditions apply.\nPayment due within 30 days.",
}


# ---------- locate_evidence ----------
def test_exact_evidence_on_cited_page():
    r = locate_evidence("Invoice No: INV-2025-0042", PAGES, 1)
    assert (r.page, r.score, r.exact, r.page_corrected) == (1, 1.0, True, False)


def test_exact_match_ignores_case_and_whitespace():
    assert locate_evidence("invoice   no:   inv-2025-0042", PAGES, 1).exact


def test_wrong_page_claim_is_corrected_not_trusted():
    r = locate_evidence("Payment due within 30 days", PAGES, 1)
    assert r.page == 2 and r.page_corrected and r.exact


def test_fabricated_evidence_not_found():
    r = locate_evidence("Invoice No: INV-9999-0001", PAGES, 1)
    assert r.score < 1.0 and r.page is None and not r.exact


def test_ocr_noise_is_approximate_not_exact():
    r = locate_evidence("Invoice No: INV-2O25-0042", PAGES, 1)  # letter O for zero, as OCR often does
    assert not r.exact and r.page == 1 and 0.85 <= r.score < 1.0


def test_empty_evidence():
    assert locate_evidence("  ", PAGES, 1).score == 0.0


def test_bengali_digits_in_evidence_match_ascii_quote():
    pages = {1: "মোট: ১,১৫০.০০ টাকা"}
    assert locate_evidence("মোট: 1,150.00 টাকা", pages, 1).exact


# ---------- value_supported ----------
@pytest.mark.parametrize("field,value,evidence,ok", [
    ("invoice_number", "INV-2025-0042", "Invoice No: INV-2025-0042", True),
    ("invoice_number", "INV-2025-0043", "Invoice No: INV-2025-0042", False),
    ("total", "1,150.00", "Total ৳ 1,150.00", True),
    ("total", "1150", "Total ৳ 1,150.00", True),            # same number, different formatting
    ("total", "1,200.00", "Total ৳ 1,150.00", False),
    ("total", "১,১৫০.০০", "Total 1,150.00", True),           # Bengali digits vs ASCII evidence
    ("total", "1,23,456.50", "Grand total 1,23,456.50 only", True),
    ("tax", "0", "VAT 0.00", True),                          # zero is a real value
    ("quantity", "10", "Qty 10 pcs", True),
    ("quantity", "1", "Qty 10 pcs", False),                  # '1' must not match inside '10'
    ("invoice_date", "15/01/2025", "Date: 15/01/2025", True),
    ("invoice_date", "2025-01-15", "Date: 15 Jan 2025", True),  # model reformatted: same date by meaning
    ("invoice_date", "2025-01-16", "Date: 15 Jan 2025", False),
    ("currency", "BDT", "Total ৳ 1,150.00", True),
    ("currency", "USD", "Total ৳ 1,150.00", False),
    ("supplier_name", "ACME Traders", "ACME Traders Ltd", True),
])
def test_value_supported(field, value, evidence, ok):
    assert value_supported(field, value, evidence) is ok


# ---------- schemas ----------
def test_schema_coerces_numbers_and_blanks():
    out = ExtractionOut.model_validate({"invoices": [{"total": {"value": 1150, "evidence": "Total 1150", "page": "1"},
                                                      "tax": {"value": "  ", "evidence": None, "page": None}}]})
    inv = out.invoices[0]
    assert inv.total.value == "1150" and inv.total.page == 1 and inv.tax.value is None
    assert inv.line_items == [] and inv.invoice_number == FieldOut()


def test_schema_accepts_bare_string_but_it_has_no_evidence():
    f = ExtractionOut.model_validate({"invoices": [{"invoice_number": "INV-1"}]}).invoices[0].invoice_number
    assert f.value == "INV-1" and f.evidence is None


def test_schema_rejects_non_object_payloads_and_bad_types():
    with pytest.raises(ValidationError):
        ExtractionOut.model_validate({"invoices": "nope"})
    with pytest.raises(ValidationError):
        ExtractionOut.model_validate({"invoices": [{"total": {"value": ["a"]}}]})


def test_schema_ignores_unknown_keys_and_null_lists():
    out = ExtractionOut.model_validate({"invoices": [{"line_items": None, "extra": 1}], "note": "x"})
    assert out.invoices[0].line_items == []


# ---------- prompt ----------
def test_prompt_loads_and_version_is_validated():
    assert "NEVER guess" in load_prompt("invoice-v1")
    with pytest.raises(ValueError):
        load_prompt("../secrets")
    with pytest.raises(FileNotFoundError):
        load_prompt("invoice-v99")


# ---------- providers ----------
def provider(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider("https://llm.test/v1/", "k", "m1", client=client, **kw)


def test_openai_provider_request_and_response():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(json.loads(req.content), url=str(req.url), auth=req.headers["authorization"])
        return httpx.Response(200, json={"model": "m1-2025", "choices": [{"message": {"content": "{}"}}],
                                         "usage": {"total_tokens": 5}})

    r = provider(handler).complete([{"role": "user", "content": "hi"}])
    assert seen["url"] == "https://llm.test/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["temperature"] == 0 and seen["response_format"] == {"type": "json_object"}
    assert (r.text, r.model, r.usage) == ("{}", "m1-2025", {"total_tokens": 5})


def test_json_mode_can_be_disabled():
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    provider(handler, json_mode=False).complete([])
    assert "response_format" not in seen


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_http_errors(status):
    with pytest.raises(LLMTransientError):
        provider(lambda r: httpx.Response(status)).complete([])


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_permanent_http_errors(status):
    with pytest.raises(LLMPermanentError):
        provider(lambda r: httpx.Response(status, text="no")).complete([])


def test_network_failure_and_bad_shape_are_transient():
    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(LLMTransientError):
        provider(boom).complete([])
    with pytest.raises(LLMTransientError):
        provider(lambda r: httpx.Response(200, json={"unexpected": 1})).complete([])


def test_misconfigured_provider_is_permanent_and_api_key_not_required():
    with pytest.raises(LLMPermanentError):
        OpenAICompatibleProvider("", "", "")
    assert OpenAICompatibleProvider("http://localhost:8000/v1", "", "local")  # self-hosted without a key


def test_mock_returns_registered_fixture_else_empty_extraction():
    prompt = "<document>x</document>"
    m = MockProvider({MockProvider.key_for(prompt): '{"invoices": [{}]}'})
    assert m.complete([{"role": "user", "content": prompt}]).text == '{"invoices": [{}]}'
    assert json.loads(m.complete([{"role": "user", "content": "other"}]).text) == {"invoices": []}


def test_get_provider_selection():
    from app.core.config import Settings
    assert isinstance(get_provider(Settings(llm_provider="mock")), MockProvider)
    assert get_provider(Settings(llm_provider="mock"), fallback=True) is None
    s = Settings(llm_provider="openai_compatible", llm_base_url="https://x/v1", llm_model="a", llm_fallback_model="b")
    assert get_provider(s).model == "a" and get_provider(s, fallback=True).model == "b"
    assert get_provider(Settings(llm_provider="openai_compatible", llm_base_url="https://x/v1", llm_model="a"),
                        fallback=True) is None
