import { describe, expect, it } from "vitest";
import type { DataField, DocType, Field } from "./api";
import {
  changedFields,
  fieldMessage,
  formatValue,
  fromFormValue,
  initialValues,
  missingRequired,
  parseApiDatetime,
  timeAgo,
  toFormValue,
} from "./values";

const field = (fieldname: string, fieldtype: Field["fieldtype"] = "Data", props: Partial<Field> = {}): DataField => ({
  fieldname,
  fieldtype,
  label: fieldname,
  ...props,
});
const doctype = (fields: Field[]): DocType => ({
  name: "deal",
  label: "Deal",
  module: "CRM",
  table: "tab_deal",
  autoname: "hash",
  fields,
  columns: [],
});

describe("datetimes", () => {
  it("round-trip through the local-time input unchanged", () => {
    const at = field("at", "Datetime");
    const shown = toFormValue(at, "2026-09-25T10:30:00+00:00");
    expect(shown).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d$/);
    expect(fromFormValue(at, shown)).toBe("2026-09-25T10:30:00.000Z");
  });

  it("without an offset are taken as UTC, as the API does", () => {
    expect(parseApiDatetime("2026-09-25T10:30:00").toISOString()).toBe("2026-09-25T10:30:00.000Z");
  });
});

describe("fromFormValue", () => {
  it("sends blanks as null, numbers as text and checkboxes as booleans", () => {
    expect(fromFormValue(field("a"), "   ")).toBeNull();
    expect(fromFormValue(field("qty", "Int"), "42")).toBe("42");
    expect(fromFormValue(field("vip", "Check"), true)).toBe(true);
    expect(fromFormValue(field("vip", "Check"), "")).toBe(false);
  });
});

describe("initialValues", () => {
  it("fills in defaults, working out Today", () => {
    const dt = doctype([
      field("stage", "Select", { options: "Open\nWon", default: "Open" }),
      field("on", "Date", { default: "Today" }),
      field("vip", "Check", { default: "1" }),
      field("note"),
    ]);
    expect(initialValues(dt, new Date(2026, 8, 22, 15, 0))).toEqual({
      stage: "Open",
      on: "2026-09-22",
      vip: true,
      note: "",
    });
  });
});

describe("changedFields and missingRequired", () => {
  const dt = doctype([field("title", "Data", { reqd: true }), field("done", "Check", { reqd: true }), field("note")]);

  it("lists only the fields that differ", () => {
    const before = { title: "Chairs", done: false, note: "" };
    expect(changedFields(dt, before, { ...before, note: "urgent" })).toEqual(["note"]);
  });

  it("flags empty required fields, but not unticked checkboxes", () => {
    expect(missingRequired(dt, { title: " ", done: false, note: "" })).toEqual({ title: "is required" });
  });
});

describe("fieldMessage", () => {
  it("puts the label in front of sentence endings only", () => {
    expect(fieldMessage("Lead Name", "is required")).toBe("Lead Name is required");
    expect(fieldMessage("Qty", "Input should be a valid integer")).toBe("Input should be a valid integer");
  });
});

describe("formatValue", () => {
  it("formats amounts the Indian way and dates without a timezone shift", () => {
    expect(formatValue(field("amount", "Currency"), 145000.5)).toBe("1,45,000.50");
    expect(formatValue(field("on", "Date"), "2026-10-15")).toBe("15 Oct 2026");
    expect(formatValue(field("vip", "Check"), true)).toBe("Yes");
    expect(formatValue(field("note"), null)).toBe("");
  });
});

describe("timeAgo", () => {
  it("speaks in the largest sensible unit", () => {
    const now = Date.parse("2026-09-22T12:00:00Z");
    expect(timeAgo("2026-09-22T11:59:30Z", now)).toBe("just now");
    expect(timeAgo("2026-09-22T11:55:00Z", now)).toBe("5 minutes ago");
    expect(timeAgo("2026-09-21T12:00:00Z", now)).toBe("yesterday");
  });
});
