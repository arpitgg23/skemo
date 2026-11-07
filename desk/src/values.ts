import type { DataField, Doc, DocType } from "./api";
import { dataFields } from "./meta";

/** What a form input holds: text for most fields, a boolean for checkboxes. */
export type FormValue = string | boolean;
export type FormValues = Record<string, FormValue>;

const pad = (n: number) => String(n).padStart(2, "0");

export const toLocalDate = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** The "YYYY-MM-DDTHH:mm" an <input type="datetime-local"> shows, in local time. */
export const toLocalDatetime = (d: Date) =>
  `${toLocalDate(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** Like the API, a datetime without an offset is taken as UTC. */
export function parseApiDatetime(value: string): Date {
  return new Date(/(Z|[+-]\d\d:?\d\d)$/i.test(value) ? value : `${value}Z`);
}

/** A stored value as its form input holds it. */
export function toFormValue(field: DataField, value: unknown): FormValue {
  if (field.fieldtype === "Check") return value === true;
  if (value === null || value === undefined) return "";
  if (field.fieldtype === "Datetime") return toLocalDatetime(parseApiDatetime(String(value)));
  return String(value);
}

/** A form input's value as the API takes it. Numbers stay text; the server converts them. */
export function fromFormValue(field: DataField, value: FormValue): unknown {
  if (field.fieldtype === "Check") return value === true;
  if (typeof value !== "string" || value.trim() === "") return null;
  if (field.fieldtype === "Datetime") return new Date(value).toISOString();
  return value;
}

export function toFormValues(doctype: DocType, doc: Doc): FormValues {
  return Object.fromEntries(
    dataFields(doctype).map((f) => [f.fieldname, toFormValue(f, doc[f.fieldname])]),
  );
}

/** Starting values for a new record: each field's default, with Today and Now worked out. */
export function initialValues(doctype: DocType, now = new Date()): FormValues {
  return Object.fromEntries(dataFields(doctype).map((f) => [f.fieldname, defaultValue(f, now)]));
}

function defaultValue(field: DataField, now: Date): FormValue {
  const value = (field.default ?? "").trim();
  const lowered = value.toLowerCase();
  if (field.fieldtype === "Check") return ["1", "true", "yes", "on"].includes(lowered);
  if (field.fieldtype === "Date" && lowered === "today") return toLocalDate(now);
  if (field.fieldtype === "Datetime" && lowered === "now") return toLocalDatetime(now);
  return value ? toFormValue(field, value) : "";
}

export function changedFields(doctype: DocType, original: FormValues, current: FormValues): string[] {
  return dataFields(doctype)
    .map((f) => f.fieldname)
    .filter((name) => original[name] !== current[name]);
}

/** Required fields left empty, caught before a round trip to the server. */
export function missingRequired(doctype: DocType, values: FormValues): Record<string, string> {
  const missing: Record<string, string> = {};
  for (const f of dataFields(doctype)) {
    const empty = String(values[f.fieldname] ?? "").trim() === "";
    if (f.reqd && f.fieldtype !== "Check" && empty) missing[f.fieldname] = "is required";
  }
  return missing;
}

/** Some server messages start lowercase ("can't delete…"); banners show them as sentences.
 * A message starting with an identifier (tab_lead: …) is left as it is. */
export function sentence(text: string): string {
  const firstWord = text.split(/\s/, 1)[0];
  if (/[_\d]/.test(firstWord)) return text;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Server messages like "is required" are sentence ends; put the field's label in front. */
export function fieldMessage(label: string, message: string): string {
  return /^[a-z]/.test(message) ? `${label} ${message}` : message;
}

// Indian number grouping (1,45,000.50) until sites get a locale setting.
const amount = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const number = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 6 });
const day = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC", // a Date field has no time, so don't let the local timezone shift it
});
const moment = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** A value as shown in a list cell. */
export function formatValue(field: DataField | undefined, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  switch (field?.fieldtype) {
    case "Check":
      return value ? "Yes" : "No";
    case "Currency":
      return amount.format(Number(value));
    case "Int":
    case "Float":
      return number.format(Number(value));
    case "Date":
      return day.format(new Date(`${String(value)}T00:00:00Z`));
    case "Datetime":
      return moment.format(parseApiDatetime(String(value)));
    default:
      return String(value);
  }
}

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** "just now", "5 minutes ago", "yesterday"… */
export function timeAgo(iso: string, now = Date.now()): string {
  let delta = Math.round((parseApiDatetime(iso).getTime() - now) / 1000);
  if (Math.abs(delta) < 60) return "just now";
  delta = Math.round(delta / 60);
  for (const [unit, size] of [
    ["minute", 60],
    ["hour", 24],
    ["day", 30],
    ["month", 12],
  ] as const) {
    if (Math.abs(delta) < size) return relative.format(delta, unit);
    delta = Math.round(delta / size);
  }
  return relative.format(delta, "year");
}
