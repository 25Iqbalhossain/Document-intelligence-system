import { describe, expect, it } from "vitest";
import { confidenceLabel, diffFields, extOk, formatMoney, humanize } from "./format";

describe("formatMoney", () => {
  it("groups and trims trailing zeros without float math", () => {
    expect(formatMoney("1150.0000")).toBe("1,150.00");
    expect(formatMoney("1234567.8900", "BDT")).toBe("1,234,567.89 BDT");
    expect(formatMoney("0.1000")).toBe("0.10");
    expect(formatMoney("12345678901234567.1250")).toBe("12,345,678,901,234,567.125"); // beyond float precision
  });
  it("keeps null distinct from zero", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney("0.0000")).toBe("0.00");
    expect(formatMoney("-5.5")).toBe("-5.50");
  });
  it("passes through unparseable text instead of inventing a number", () => {
    expect(formatMoney("N/A")).toBe("N/A");
  });
});

describe("diffFields", () => {
  it("sends only changed fields and null for cleared ones", () => {
    expect(diffFields({ total: "100.0000", tax: null }, { total: "100.0000", tax: "" })).toEqual({});
    expect(diffFields({ total: "100", supplier_name: "A" }, { total: "101", supplier_name: "" }))
      .toEqual({ total: "101", supplier_name: null });
  });
});

describe("misc", () => {
  it("confidence buckets", () => {
    expect(confidenceLabel(0.9)).toBe("high");
    expect(confidenceLabel(0.7)).toBe("medium");
    expect(confidenceLabel(0.2)).toBe("low");
    expect(confidenceLabel(null)).toBe("none");
  });
  it("extension check and humanize", () => {
    expect(extOk("A.PDF")).toBe(true);
    expect(extOk("a.exe")).toBe(false);
    expect(humanize("needs_review")).toBe("Needs review");
  });
});
