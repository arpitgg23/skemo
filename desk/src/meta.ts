import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, namePath, type DataField, type DocType, type Field } from "./api";

/** Every DocType on the site. Refetched when the window regains focus, so a DocType saved
 * from the CLI shows up without a reload. */
export function useDocTypes() {
  return useQuery({ queryKey: ["doctypes"], queryFn: api.doctypes });
}

export function useDocType(name: string | undefined) {
  const { data, isLoading, isFetching, error, refetch } = useDocTypes();
  const doctype = data?.doctypes.find((dt) => dt.name === name);
  const unknown = Boolean(name && data && !doctype);
  // A DocType we haven't seen may have just been created: ask the server once more before
  // calling it "not found".
  useEffect(() => {
    if (unknown) void refetch();
  }, [unknown, name, refetch]);
  return { doctype, isLoading: isLoading || (unknown && isFetching), error };
}

export const docUrl = (doctype: string, name: string) => `/app/${doctype}/${namePath(name)}`;

export const isDataField = (f: Field): f is DataField =>
  f.fieldtype !== "Section Break" && f.fieldtype !== "Column Break" && Boolean(f.fieldname);

export const dataFields = (doctype: DocType): DataField[] => doctype.fields.filter(isDataField);

export const choices = (field: Field): string[] =>
  (field.options ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

/** List view columns: the fields marked in_list_view, or the first four if none are. */
export function listFields(doctype: DocType): DataField[] {
  const fields = dataFields(doctype);
  const marked = fields.filter((f) => f.in_list_view);
  return marked.length ? marked : fields.slice(0, 4);
}

/** The field that describes a record in words, like customer_name, for search and links. */
export function titleField(doctype: DocType): DataField | undefined {
  const text = dataFields(doctype).filter((f) => f.fieldtype === "Data");
  return text.find((f) => f.in_list_view) ?? text.find((f) => f.reqd) ?? text[0];
}

export type Section = { label?: string; columns: DataField[][] };

/** The form layout: a Section Break starts a section and a Column Break a new column in it.
 * Hidden fields, empty columns and empty sections are dropped. */
export function formLayout(fields: Field[]): Section[] {
  const sections: Section[] = [];
  let current: Section | undefined;
  for (const field of fields) {
    if (field.fieldtype === "Section Break") {
      current = { label: field.label, columns: [[]] };
      sections.push(current);
      continue;
    }
    if (!current) {
      current = { columns: [[]] };
      sections.push(current);
    }
    if (field.fieldtype === "Column Break") current.columns.push([]);
    else if (isDataField(field) && !field.hidden) current.columns[current.columns.length - 1].push(field);
  }
  return sections
    .map((s) => ({ ...s, columns: s.columns.filter((c) => c.length > 0) }))
    .filter((s) => s.columns.length > 0);
}

export function groupByModule(doctypes: DocType[]): [string, DocType[]][] {
  const groups = new Map<string, DocType[]>();
  for (const dt of doctypes) groups.set(dt.module, [...(groups.get(dt.module) ?? []), dt]);
  return [...groups];
}
