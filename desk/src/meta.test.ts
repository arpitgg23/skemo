import { describe, expect, it } from "vitest";
import type { DocType, Field } from "./api";
import { formLayout, listFields, titleField } from "./meta";

const field = (fieldname: string, fieldtype: Field["fieldtype"] = "Data", props: Partial<Field> = {}): Field => ({
  fieldname,
  fieldtype,
  label: fieldname,
  ...props,
});
const section = (label?: string): Field => ({ fieldtype: "Section Break", label });
const column: Field = { fieldtype: "Column Break" };
const doctype = (fields: Field[]): DocType => ({
  name: "deal",
  label: "Deal",
  module: "CRM",
  table: "tab_deal",
  autoname: "hash",
  fields,
  columns: [],
});

const shape = (fields: Field[]) =>
  formLayout(fields).map((s) => ({
    label: s.label,
    columns: s.columns.map((c) => c.map((f) => f.fieldname)),
  }));

describe("formLayout", () => {
  it("puts fields before any break into one untitled section", () => {
    expect(shape([field("a"), field("b")])).toEqual([{ label: undefined, columns: [["a", "b"]] }]);
  });

  it("starts sections and columns at breaks, dropping hidden fields and empty parts", () => {
    const fields = [
      column, // an empty first column is dropped
      field("a"),
      column,
      field("b"),
      field("secret", "Data", { hidden: true }),
      section("Notes"),
      field("notes", "Text"),
      section("Empty"),
    ];
    expect(shape(fields)).toEqual([
      { label: undefined, columns: [["a"], ["b"]] },
      { label: "Notes", columns: [["notes"]] },
    ]);
  });
});

describe("listFields", () => {
  it("uses the fields marked in_list_view", () => {
    const dt = doctype([field("a"), field("b", "Int", { in_list_view: true }), section(), field("c")]);
    expect(listFields(dt).map((f) => f.fieldname)).toEqual(["b"]);
  });

  it("falls back to the first four fields", () => {
    const dt = doctype(["a", "b", "c", "d", "e"].map((n) => field(n)));
    expect(listFields(dt).map((f) => f.fieldname)).toEqual(["a", "b", "c", "d"]);
  });
});

describe("titleField", () => {
  it("prefers a Data field shown in the list, then a required one", () => {
    const listed = doctype([field("amount", "Currency", { in_list_view: true }), field("title", "Data", { in_list_view: true })]);
    expect(titleField(listed)?.fieldname).toBe("title");
    const required = doctype([field("note"), field("customer_name", "Data", { reqd: true })]);
    expect(titleField(required)?.fieldname).toBe("customer_name");
    expect(titleField(doctype([field("qty", "Int")]))).toBeUndefined();
  });
});
