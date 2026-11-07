import { describe, expect, it } from "vitest";
import type { DocType, Field } from "../api";
import {
  type Action,
  allFields,
  type Draft,
  draftProblems,
  reduce,
  toDraft,
  toIdentifier,
  toSave,
  uniqueName,
} from "./model";

const deal: DocType = {
  name: "deal",
  label: "Deal",
  module: "CRM",
  table: "tab_deal",
  autoname: "format:DEAL-{####}",
  can: ["read", "write", "create", "delete"],
  columns: [],
  fields: [
    { fieldname: "title", fieldtype: "Data", label: "Title", reqd: true },
    { fieldtype: "Column Break" },
    { fieldname: "amount", fieldtype: "Currency", label: "Amount" },
    { fieldtype: "Section Break", label: "Notes" },
    { fieldname: "notes", fieldtype: "Text", label: "Notes" },
  ],
};

const run = (draft: Draft, ...actions: Action[]) => actions.reduce(reduce, draft);
const names = (draft: Draft) => allFields(draft).map((f) => f.fieldname);
const firstColumn = (draft: Draft, section = 0) => draft.sections[section].columns[0].key;

describe("toDraft and toSave", () => {
  it("round-trip a DocType's fields and layout unchanged", () => {
    const draft = toDraft(deal);
    expect(draft.sections.map((s) => [s.label, s.columns.length])).toEqual([
      ["", 2],
      ["Notes", 1],
    ]);
    const plan = toSave(draft);
    expect(plan.definition.fields).toEqual(deal.fields);
    expect(plan.renames).toEqual({});
    expect(plan.keys).toHaveLength(deal.fields.length);
  });

  it("report renamed fields, but not new ones", () => {
    const draft = toDraft(deal);
    const title = allFields(draft)[0];
    const renamed = run(
      draft,
      { type: "updateField", key: title.key, changes: { fieldname: "deal_title" } },
      { type: "addField", key: "new", fieldtype: "Int", columnKey: firstColumn(draft) },
    );
    expect(toSave(renamed).renames).toEqual({ title: "deal_title" });
  });
});

describe("naming", () => {
  it("turns labels into fieldnames", () => {
    expect(toIdentifier("Customer Name")).toBe("customer_name");
    expect(toIdentifier("  GST No. (15 chars) ")).toBe("gst_no_15_chars");
    expect(toIdentifier("2nd phone")).toBe("f_2nd_phone");
    expect(toIdentifier("!!!")).toBe("field");
  });

  it("avoids taken and reserved names", () => {
    expect(uniqueName("phone", new Set(["phone", "phone_2"]))).toBe("phone_3");
    expect(uniqueName("name", new Set())).toBe("name_2");
  });

  it("lets a new field's fieldname follow its label until it's edited by hand", () => {
    let draft = toDraft(deal);
    const column = firstColumn(draft);
    draft = run(draft, { type: "addField", key: "f", fieldtype: "Data", columnKey: column });
    expect(allFields(draft).find((f) => f.key === "f")?.fieldname).toBe("short_text");
    draft = run(draft, { type: "updateField", key: "f", changes: { label: "Title" } });
    expect(allFields(draft).find((f) => f.key === "f")?.fieldname).toBe("title_2"); // "title" is taken
    draft = run(
      draft,
      { type: "updateField", key: "f", changes: { fieldname: "headline" } },
      { type: "updateField", key: "f", changes: { label: "Something else" } },
    );
    expect(allFields(draft).find((f) => f.key === "f")?.fieldname).toBe("headline");
  });

  it("keeps an existing field's fieldname when its label changes", () => {
    const draft = toDraft(deal);
    const title = allFields(draft)[0];
    const relabelled = run(draft, { type: "updateField", key: title.key, changes: { label: "Deal title" } });
    expect(names(relabelled)[0]).toBe("title");
  });

  it("names a new DocType after its label until the name is edited", () => {
    let draft = run(toDraft(), { type: "doc", changes: { label: "Sales Invoice" } });
    expect(draft.name).toBe("sales_invoice");
    draft = run(draft, { type: "doc", changes: { name: "invoice" } }, { type: "doc", changes: { label: "Bill" } });
    expect(draft.name).toBe("invoice");
  });
});

describe("layout edits", () => {
  it("move fields within and across columns", () => {
    const draft = toDraft(deal);
    const [title, amount] = allFields(draft);
    const moved = run(draft, { type: "moveField", key: title.key, columnKey: draft.sections[0].columns[1].key, index: 1 });
    expect(moved.sections[0].columns.map((c) => c.fields.map((f) => f.fieldname))).toEqual([[], ["amount", "title"]]);
    const back = run(moved, { type: "moveField", key: title.key, columnKey: draft.sections[0].columns[1].key, index: 0 });
    expect(back.sections[0].columns[1].fields.map((f) => f.key)).toEqual([title.key, amount.key]);
  });

  it("keep the fields of a removed section or column", () => {
    const draft = toDraft(deal);
    const withoutNotes = run(draft, { type: "removeSection", key: draft.sections[1].key });
    expect(withoutNotes.sections).toHaveLength(1);
    expect(names(withoutNotes)).toEqual(["title", "amount", "notes"]);

    const section = draft.sections[0];
    const oneColumn = run(draft, { type: "removeColumn", sectionKey: section.key, columnKey: section.columns[1].key });
    expect(oneColumn.sections[0].columns.map((c) => c.fields.map((f) => f.fieldname))).toEqual([["title", "amount"]]);
  });

  it("refuse edits that don't apply", () => {
    const draft = toDraft();
    expect(run(draft, { type: "removeSection", key: draft.sections[0].key })).toBe(draft); // the last one
    expect(run(draft, { type: "moveSection", key: draft.sections[0].key, delta: -1 })).toBe(draft);
  });

  it("clear properties that don't suit a new field type", () => {
    let draft = toDraft({ ...deal, fields: [{ fieldname: "code", fieldtype: "Data", length: 10, default: "X" } as Field] });
    const code = allFields(draft)[0];
    draft = run(draft, { type: "changeType", key: code.key, fieldtype: "Select" });
    expect(allFields(draft)[0]).toMatchObject({ fieldtype: "Select", options: "Option 1\nOption 2" });
    expect(allFields(draft)[0]).not.toHaveProperty("length");
    expect(allFields(draft)[0]).not.toHaveProperty("default");
  });
});

describe("draftProblems", () => {
  it("flags what the server would refuse", () => {
    let draft = run(toDraft(), { type: "doc", changes: { label: "Visit" } });
    const column = firstColumn(draft);
    draft = run(
      draft,
      { type: "addField", key: "a", fieldtype: "Data", columnKey: column },
      { type: "addField", key: "b", fieldtype: "Link", columnKey: column },
      { type: "addField", key: "c", fieldtype: "Select", columnKey: column },
      { type: "updateField", key: "c", changes: { options: "  " } },
      { type: "addField", key: "d", fieldtype: "Data", columnKey: column },
      { type: "updateField", key: "d", changes: { fieldname: "short_text" } },
    );
    expect(draftProblems(draft, ["visit"])).toEqual({
      doctype: 'A DocType named "visit" already exists.',
      b: "Choose the DocType it links to",
      c: "Add at least one choice",
      d: 'Another field is already called "short_text"',
    });
  });
});
