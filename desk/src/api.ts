export type FieldType =
  | "Data"
  | "Text"
  | "Int"
  | "Float"
  | "Currency"
  | "Date"
  | "Datetime"
  | "Check"
  | "Select"
  | "Link"
  | "Section Break"
  | "Column Break";

export type Field = {
  fieldname?: string;
  label?: string;
  fieldtype: FieldType;
  options?: string;
  length?: number;
  reqd?: boolean;
  unique?: boolean;
  search_index?: boolean;
  in_list_view?: boolean;
  read_only?: boolean;
  hidden?: boolean;
  default?: string;
  description?: string;
};

/** A field that holds a value (not a layout break), so it always has a fieldname. */
export type DataField = Field & { fieldname: string };

export type Column = { name: string; type: string; nullable: boolean };

export type DocType = {
  name: string;
  label: string;
  module: string;
  table: string;
  autoname: string;
  fields: Field[];
  columns: Column[]; // what the table really has in Postgres
};

/** One record: its name, its field values, and who changed it when. */
export type Doc = {
  name: string;
  owner: string;
  created_at: string;
  modified_at: string;
  modified_by: string;
  [fieldname: string]: unknown;
};

export type FieldError = { field: string; message: string };

export type LogEntry = {
  batch_id: string;
  created_at: string;
  actor: string;
  table: string;
  statement: string;
};

export type Filter = [field: string, operator: string, value: unknown];

export type ListParams = {
  fields?: string[];
  filters?: Filter[];
  orFilters?: Filter[];
  orderBy?: string;
  limit?: number;
  offset?: number;
};

export class ApiError extends Error {
  readonly status: number;
  /** Per-field problems, sent with 422. */
  readonly errors: FieldError[];
  /** Why a 409 happened: "duplicate", "linked" or "stale". */
  readonly code?: string;

  constructor(status: number, message: string, errors: FieldError[] = [], code?: string) {
    super(message);
    this.status = status;
    this.errors = errors;
    this.code = code;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the API. Is `dhancha serve` running?");
  }
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof payload?.detail === "string" ? payload.detail : `${response.status} ${response.statusText}`;
    throw new ApiError(response.status, detail, payload?.errors ?? [], payload?.code);
  }
  return payload as T;
}

/** Record names may contain slashes (INV/24-25/001); each part is encoded separately. */
export const namePath = (name: string) => name.split("/").map(encodeURIComponent).join("/");

const resource = (doctype: string, name?: string) =>
  `/api/resource/${encodeURIComponent(doctype)}${name === undefined ? "" : `/${namePath(name)}`}`;

function listQuery(params: ListParams): string {
  const query = new URLSearchParams();
  if (params.fields) query.set("fields", params.fields.join(","));
  if (params.filters?.length) query.set("filters", JSON.stringify(params.filters));
  if (params.orFilters?.length) query.set("or_filters", JSON.stringify(params.orFilters));
  if (params.orderBy) query.set("order_by", params.orderBy);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  return query.toString();
}

export const api = {
  health: () => request<{ status: string; version: string }>("GET", "/api/health"),
  doctypes: () => request<{ site: string; doctypes: DocType[] }>("GET", "/api/meta/doctypes"),
  schemaLog: (limit = 20) =>
    request<{ site: string; entries: LogEntry[] }>("GET", `/api/meta/schema-log?limit=${limit}`),
  list: (doctype: string, params: ListParams = {}) =>
    request<{ data: Doc[]; total: number }>("GET", `${resource(doctype)}?${listQuery(params)}`),
  get: (doctype: string, name: string) =>
    request<{ data: Doc }>("GET", resource(doctype, name)).then((r) => r.data),
  create: (doctype: string, values: Record<string, unknown>) =>
    request<{ data: Doc }>("POST", resource(doctype), values).then((r) => r.data),
  update: (doctype: string, name: string, values: Record<string, unknown>) =>
    request<{ data: Doc }>("PUT", resource(doctype, name), values).then((r) => r.data),
  remove: (doctype: string, name: string) => request<void>("DELETE", resource(doctype, name)),
};
