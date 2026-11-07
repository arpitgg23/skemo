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
  | "Table"
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

export type Action = "read" | "write" | "create" | "delete";

export type CurrentUser = {
  name: string;
  full_name: string;
  label: string;
  roles: string[];
  is_admin: boolean;
};

/** Who's signed in, on which site. Signing in and /api/auth/me both return this. */
export type Session = { site: string; developer_mode: boolean; user: CurrentUser };

export type DocType = {
  name: string;
  label: string;
  module: string;
  table: string;
  autoname: string;
  description?: string | null;
  app?: string | null; // the app whose files it comes from; null means the builder made it
  is_child?: boolean; // holds the rows of a Table field, rather than standing on its own
  can: Action[]; // what the signed-in user may do with it
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

/** A DocType as the API takes it when saving: no live columns, most properties optional. */
export type DocTypeDefinition = {
  name: string;
  label?: string;
  module?: string;
  autoname?: string;
  description?: string;
  is_child?: boolean;
  fields: Field[];
};

/** The DDL a save or delete ran, how many values were archived before dropping, and the
 * app file the DocType was written back to (developer mode only). */
export type SchemaChange = { statements: string[]; archived_rows: number; written_to?: string | null };

export class ApiError extends Error {
  readonly status: number;
  /** Per-field problems, sent with 422. */
  readonly errors: FieldError[];
  /** Why a 409 happened: "duplicate", "linked" or "stale". */
  readonly code?: string;
  /** The whole error response, for callers that need more than the above. */
  readonly body: unknown;

  constructor(status: number, message: string, errors: FieldError[] = [], code?: string, body?: unknown) {
    super(message);
    this.status = status;
    this.errors = errors;
    this.code = code;
    this.body = body;
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
    throw new ApiError(0, "Can't reach the API. Is `skemo serve` running?");
  }
  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      typeof payload?.detail === "string" ? payload.detail : `${response.status} ${response.statusText}`;
    throw new ApiError(response.status, detail, payload?.errors ?? [], payload?.code, payload);
  }
  return payload as T;
}

/** Record names may contain slashes (INV/24-25/001); each part is encoded separately. */
export const namePath = (name: string) => name.split("/").map(encodeURIComponent).join("/");

/** The printable page for a record, opened in its own tab. */
export const printUrl = (doctype: string, name: string) =>
  `/api/print/${encodeURIComponent(doctype)}/${namePath(name)}`;

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
  login: (user: string, password: string) =>
    request<Session>("POST", "/api/auth/login", { user, password }),
  logout: () => request<void>("POST", "/api/auth/logout"),
  me: () => request<Session>("GET", "/api/auth/me"),
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
  /** Create or update a DocType; its table is synced in the same request. */
  saveDocType: (
    name: string,
    body: { doctype: DocTypeDefinition; renames: Record<string, string>; create: boolean },
  ) =>
    request<SchemaChange & { doctype: DocTypeDefinition }>(
      "PUT",
      `/api/meta/doctype/${encodeURIComponent(name)}`,
      body,
    ),
  deleteDocType: (name: string) =>
    request<SchemaChange>("DELETE", `/api/meta/doctype/${encodeURIComponent(name)}`),
};
