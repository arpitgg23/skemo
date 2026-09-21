export type Field = {
  fieldname?: string;
  label?: string;
  fieldtype: string;
  options?: string;
  reqd?: boolean;
  unique?: boolean;
};

export type Column = { name: string; type: string; nullable: boolean };

export type DocType = {
  name: string;
  label: string;
  module: string;
  table: string;
  fields: Field[];
  columns: Column[]; // what the table really has in Postgres
};

export type LogEntry = {
  batch_id: string;
  created_at: string;
  actor: string;
  table: string;
  statement: string;
};

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => get<{ status: string; version: string }>("/api/health"),
  doctypes: () => get<{ site: string; doctypes: DocType[] }>("/api/meta/doctypes"),
  schemaLog: (limit = 20) =>
    get<{ site: string; entries: LogEntry[] }>(`/api/meta/schema-log?limit=${limit}`),
};
