/**
 * The builder's working copy of a DocType, and every edit it can make, as pure functions.
 *
 * On the server a DocType is a flat list of fields where Section Break and Column Break
 * mark the layout. For dragging things around, the builder holds it as sections, each with
 * columns, each with fields, and flattens it back on save.
 */
import type { DocType, DocTypeDefinition, Field, FieldType } from "../api";
import { choices } from "../meta";

export type BuilderField = Field & {
  key: string; // stable identity while editing
  original?: string; // fieldname as loaded; a different fieldname at save time is a rename
  fieldnameTouched?: boolean; // edited by hand, so it stops following the label
};
export type Column = { key: string; fields: BuilderField[] };
export type Section = { key: string; label: string; columns: Column[] };
export type Draft = {
  isNew: boolean;
  name: string;
  nameTouched: boolean;
  label: string;
  module: string;
  autoname: string;
  description: string;
  sections: Section[];
};

export type Selection =
  | { kind: "doctype" }
  | { kind: "section"; key: string }
  | { kind: "field"; key: string };

/** What saving sends, plus which builder element each flattened field came from, so the
 * server's errors (which point at field indexes) land on the right card. */
export type SavePlan = { definition: DocTypeDefinition; renames: Record<string, string>; keys: string[] };

export const MAX_COLUMNS = 4;
const IDENTIFIER = /^[a-z][a-z0-9_]*$/;
const RESERVED = new Set(["id", "name", "owner", "created_at", "modified_at", "modified_by"]);

/** Every field type, named the way a business user would say it. */
export const PALETTE: { fieldtype: FieldType; label: string }[] = [
  { fieldtype: "Data", label: "Short text" },
  { fieldtype: "Text", label: "Long text" },
  { fieldtype: "Int", label: "Whole number" },
  { fieldtype: "Float", label: "Decimal number" },
  { fieldtype: "Currency", label: "Amount" },
  { fieldtype: "Date", label: "Date" },
  { fieldtype: "Datetime", label: "Date and time" },
  { fieldtype: "Check", label: "Checkbox" },
  { fieldtype: "Select", label: "Dropdown" },
  { fieldtype: "Link", label: "Link" },
];

export const typeLabel = (fieldtype: FieldType) =>
  PALETTE.find((p) => p.fieldtype === fieldtype)?.label ?? fieldtype;

let lastKey = 0;
export const newKey = () => `b${++lastKey}`;
const newColumn = (fields: BuilderField[] = []): Column => ({ key: newKey(), fields });
const newSection = (label = "", key = newKey()): Section => ({ key, label, columns: [newColumn()] });

/** "Customer Name" -> "customer_name": lowercase letters, digits and _, starting with a letter. */
export function toIdentifier(text: string, maxLength = 60): string {
  const id = text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+/, "")
    .slice(0, maxLength)
    .replace(/_+$/, "");
  if (!id) return "field";
  return /^[a-z]/.test(id) ? id : `f_${id}`;
}

/** `base`, or base_2, base_3… when it's taken or reserved for a standard column. */
export function uniqueName(base: string, taken: Set<string>): string {
  let candidate = base;
  for (let n = 2; RESERVED.has(candidate) || taken.has(candidate); n++) candidate = `${base}_${n}`;
  return candidate;
}

export const allFields = (draft: Draft): BuilderField[] =>
  draft.sections.flatMap((s) => s.columns.flatMap((c) => c.fields));

export function toDraft(doctype?: DocType | DocTypeDefinition): Draft {
  const sections: Section[] = [];
  let section: Section | undefined;
  for (const field of doctype?.fields ?? []) {
    if (field.fieldtype === "Section Break") {
      section = newSection(field.label ?? "");
      sections.push(section);
      continue;
    }
    if (!section) {
      section = newSection();
      sections.push(section);
    }
    if (field.fieldtype === "Column Break") section.columns.push(newColumn());
    else {
      const column = section.columns[section.columns.length - 1];
      column.fields.push({ ...field, key: newKey(), original: field.fieldname });
    }
  }
  return {
    isNew: !doctype,
    name: doctype?.name ?? "",
    nameTouched: false,
    label: doctype?.label ?? "",
    module: doctype?.module ?? "",
    autoname: doctype?.autoname ?? "hash",
    description: doctype?.description ?? "",
    sections: sections.length ? sections : [newSection()],
  };
}

/** Flatten the draft into what the API saves. */
export function toSave(draft: Draft): SavePlan {
  const fields: Field[] = [];
  const keys: string[] = [];
  const renames: Record<string, string> = {};
  draft.sections.forEach((section, i) => {
    const label = section.label.trim();
    if (i > 0 || label) {
      fields.push(label ? { fieldtype: "Section Break", label } : { fieldtype: "Section Break" });
      keys.push(section.key);
    }
    section.columns.forEach((column, j) => {
      if (j > 0) {
        fields.push({ fieldtype: "Column Break" });
        keys.push(column.key);
      }
      for (const { key, original, fieldnameTouched: _, ...field } of column.fields) {
        fields.push(compact(field));
        keys.push(key);
        if (original && field.fieldname && original !== field.fieldname) {
          renames[original] = field.fieldname;
        }
      }
    });
  });
  const definition: DocTypeDefinition = {
    name: draft.name,
    module: draft.module.trim() || "Core",
    autoname: draft.autoname,
    fields,
  };
  if (draft.label.trim()) definition.label = draft.label.trim();
  if (draft.description.trim()) definition.description = draft.description.trim();
  return { definition, renames, keys };
}

/** For "has anything changed?": what would be saved, ignoring builder-only keys. */
export const fingerprint = (plan: SavePlan) => JSON.stringify([plan.definition, plan.renames]);

/** Empty and false properties are the defaults; dropping them keeps the JSON readable. */
function compact(field: Field): Field {
  const entries = Object.entries(field).filter(([, v]) => v !== undefined && v !== "" && v !== false);
  return Object.fromEntries(entries) as Field;
}

/** Problems to fix before saving, by element key ("doctype" for the DocType itself). */
export function draftProblems(draft: Draft, knownDoctypes: string[]): Record<string, string> {
  const problems: Record<string, string> = {};
  if (!IDENTIFIER.test(draft.name) || draft.name.length > 59) {
    problems.doctype = "Give the DocType a name: lowercase letters, digits and _, starting with a letter.";
  } else if (draft.isNew && knownDoctypes.includes(draft.name)) {
    problems.doctype = `A DocType named "${draft.name}" already exists.`;
  }
  const fields = allFields(draft);
  const seen = new Set<string>();
  for (const f of fields) {
    const name = f.fieldname ?? "";
    let problem: string | undefined;
    if (!IDENTIFIER.test(name) || name.length > 63) {
      problem = "Fieldname: lowercase letters, digits and _, starting with a letter";
    } else if (RESERVED.has(name)) problem = `"${name}" is taken by a standard column`;
    else if (seen.has(name)) problem = `Another field is already called "${name}"`;
    else if (f.fieldtype === "Select" && choices(f).length === 0) problem = "Add at least one choice";
    else if (f.fieldtype === "Link" && !f.options) problem = "Choose the DocType it links to";
    else if (f.fieldtype === "Link" && f.options !== draft.name && !knownDoctypes.includes(f.options ?? "")) {
      problem = `There's no DocType named "${f.options}"`;
    }
    if (problem) problems[f.key] = problem;
    seen.add(name);
  }
  const [kind, arg = ""] = splitAutoname(draft.autoname);
  if (kind === "field" && !fields.some((f) => f.fieldname === arg)) {
    problems.doctype ??= "Record names come from a field: pick which one.";
  } else if (kind === "format" && (arg.match(/\{#+\}/g) ?? []).length !== 1) {
    problems.doctype ??= "The naming pattern needs exactly one counter, like {#####}.";
  }
  return problems;
}

/** "format:INV-{#####}" -> ["format", "INV-{#####}"]; "hash" -> ["hash"]. */
export function splitAutoname(autoname: string): [string, string?] {
  const at = autoname.indexOf(":");
  return at < 0 ? [autoname] : [autoname.slice(0, at), autoname.slice(at + 1)];
}

export type Action =
  | { type: "doc"; changes: Partial<Pick<Draft, "label" | "name" | "module" | "autoname" | "description">> }
  | { type: "addField"; key: string; fieldtype: FieldType; columnKey: string; index?: number }
  | { type: "updateField"; key: string; changes: Partial<Field> }
  | { type: "changeType"; key: string; fieldtype: FieldType }
  | { type: "removeField"; key: string }
  | { type: "moveField"; key: string; columnKey: string; index: number }
  | { type: "addSection"; key: string; afterKey?: string }
  | { type: "updateSection"; key: string; label: string }
  | { type: "moveSection"; key: string; delta: -1 | 1 }
  | { type: "removeSection"; key: string }
  | { type: "addColumn"; sectionKey: string }
  | { type: "removeColumn"; sectionKey: string; columnKey: string }
  | { type: "reset"; draft: Draft };

export function reduce(draft: Draft, action: Action): Draft {
  if (action.type === "reset") return action.draft;
  const next = structuredClone(draft);
  return apply(next, action) ? next : draft;
}

/** Applies `action` to `draft` in place; false when it doesn't apply (nothing changes). */
function apply(draft: Draft, action: Exclude<Action, { type: "reset" }>): boolean {
  switch (action.type) {
    case "doc": {
      Object.assign(draft, action.changes);
      if (action.changes.name !== undefined) draft.nameTouched = true;
      else if (action.changes.label !== undefined && draft.isNew && !draft.nameTouched) {
        draft.name = action.changes.label.trim() ? toIdentifier(action.changes.label, 59) : "";
      }
      return true;
    }
    case "addField": {
      const column = findColumn(draft, action.columnKey);
      if (!column) return false;
      const label = typeLabel(action.fieldtype);
      const taken = new Set(allFields(draft).map((f) => f.fieldname ?? ""));
      const field: BuilderField = {
        key: action.key,
        fieldtype: action.fieldtype,
        label,
        fieldname: uniqueName(toIdentifier(label), taken),
      };
      if (action.fieldtype === "Select") field.options = "Option 1\nOption 2";
      column.fields.splice(action.index ?? column.fields.length, 0, field);
      return true;
    }
    case "updateField": {
      const field = findField(draft, action.key);
      if (!field) return false;
      Object.assign(field, action.changes);
      if (action.changes.fieldname !== undefined) field.fieldnameTouched = true;
      else if (action.changes.label !== undefined && !field.original && !field.fieldnameTouched) {
        // A new field's fieldname follows its label until someone edits it by hand.
        const taken = new Set(allFields(draft).filter((f) => f !== field).map((f) => f.fieldname ?? ""));
        const base = toIdentifier(action.changes.label || typeLabel(field.fieldtype));
        field.fieldname = uniqueName(base, taken);
      }
      return true;
    }
    case "changeType": {
      const field = findField(draft, action.key);
      if (!field || field.fieldtype === action.fieldtype) return false;
      field.fieldtype = action.fieldtype;
      delete field.default; // an old default rarely suits the new type
      if (action.fieldtype !== "Data") delete field.length;
      if (action.fieldtype === "Select") field.options = "Option 1\nOption 2";
      else delete field.options; // Link needs its target chosen afresh
      return true;
    }
    case "removeField": {
      const column = columnOf(draft, action.key);
      if (!column) return false;
      column.fields = column.fields.filter((f) => f.key !== action.key);
      return true;
    }
    case "moveField": {
      const source = columnOf(draft, action.key);
      const target = findColumn(draft, action.columnKey);
      if (!source || !target) return false;
      const [field] = source.fields.splice(
        source.fields.findIndex((f) => f.key === action.key),
        1,
      );
      target.fields.splice(Math.min(action.index, target.fields.length), 0, field);
      return true;
    }
    case "addSection": {
      const after = draft.sections.findIndex((s) => s.key === action.afterKey);
      const at = after < 0 ? draft.sections.length : after + 1;
      draft.sections.splice(at, 0, newSection("New section", action.key));
      return true;
    }
    case "updateSection": {
      const section = draft.sections.find((s) => s.key === action.key);
      if (!section) return false;
      section.label = action.label;
      return true;
    }
    case "moveSection": {
      const from = draft.sections.findIndex((s) => s.key === action.key);
      const to = from + action.delta;
      if (from < 0 || to < 0 || to >= draft.sections.length) return false;
      [draft.sections[from], draft.sections[to]] = [draft.sections[to], draft.sections[from]];
      return true;
    }
    case "removeSection": {
      const at = draft.sections.findIndex((s) => s.key === action.key);
      if (at < 0 || draft.sections.length === 1) return false;
      const [removed] = draft.sections.splice(at, 1);
      // Its fields aren't deleted: they join the section before (or, for the first, after).
      const moved = removed.columns.flatMap((c) => c.fields);
      const neighbour = draft.sections[Math.max(0, at - 1)];
      if (at > 0) neighbour.columns[neighbour.columns.length - 1].fields.push(...moved);
      else neighbour.columns[0].fields.unshift(...moved);
      return true;
    }
    case "addColumn": {
      const section = draft.sections.find((s) => s.key === action.sectionKey);
      if (!section || section.columns.length >= MAX_COLUMNS) return false;
      section.columns.push(newColumn());
      return true;
    }
    case "removeColumn": {
      const section = draft.sections.find((s) => s.key === action.sectionKey);
      const at = section?.columns.findIndex((c) => c.key === action.columnKey) ?? -1;
      if (!section || at < 0 || section.columns.length === 1) return false;
      const [removed] = section.columns.splice(at, 1);
      // Its fields move to the column on its left (or right, for the first column).
      const neighbour = section.columns[Math.max(0, at - 1)];
      if (at > 0) neighbour.fields.push(...removed.fields);
      else neighbour.fields.unshift(...removed.fields);
      return true;
    }
  }
}

export function findField(draft: Draft, key: string): BuilderField | undefined {
  return allFields(draft).find((f) => f.key === key);
}

function findColumn(draft: Draft, key: string): Column | undefined {
  return draft.sections.flatMap((s) => s.columns).find((c) => c.key === key);
}

function columnOf(draft: Draft, fieldKey: string): Column | undefined {
  return draft.sections.flatMap((s) => s.columns).find((c) => c.fields.some((f) => f.key === fieldKey));
}

/** Where a drop target is: a column (drop at its end) or a field (drop in its place). */
export function locate(draft: Draft, id: string): { columnKey: string; index: number } | undefined {
  for (const column of draft.sections.flatMap((s) => s.columns)) {
    if (column.key === id) return { columnKey: column.key, index: column.fields.length };
    const index = column.fields.findIndex((f) => f.key === id);
    if (index >= 0) return { columnKey: column.key, index };
  }
  return undefined;
}
