import type { DataField, DocType, LinkTitles } from "../api";
import { dataFields } from "../meta";
import { emptyRow, type GridRow } from "../values";
import { FieldInput } from "./FieldInput";

type Props = {
  field: DataField;
  child: DocType | undefined;
  rows: GridRow[];
  error?: string;
  /** Titles behind the record's Link values; keyed by value, so reordering rows is safe. */
  titles?: LinkTitles;
  disabled?: boolean;
  onChange: (rows: GridRow[]) => void;
};

/** The rows of a child table: one line per row, one input per field of the child. */
export function GridInput({ field, child, rows, error, titles, disabled, onChange }: Props) {
  const label = field.label || field.fieldname;
  if (!child) {
    return (
      <div className="field">
        <label>{label}</label>
        <p className="error-text">Its child DocType ({field.options}) isn't here.</p>
      </div>
    );
  }
  const columns = dataFields(child);

  const change = (index: number, fieldname: string, value: string | boolean) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, [fieldname]: value } : row)));
  const move = (index: number, by: -1 | 1) => {
    const next = [...rows];
    [next[index], next[index + by]] = [next[index + by], next[index]];
    onChange(next);
  };

  return (
    <div className={`field grid-field${error ? " has-error" : ""}`}>
      <label>
        {label}
        {rows.length > 0 && <span className="muted small"> · {rows.length}</span>}
      </label>
      {rows.length > 0 && (
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="grid-index">#</th>
                {columns.map((f) => (
                  <th key={f.fieldname}>
                    {f.label || f.fieldname}
                    {f.reqd && <span className="required"> *</span>}
                  </th>
                ))}
                {!disabled && <th className="grid-actions" aria-label="Row actions" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index}>
                  <td className="grid-index">{index + 1}</td>
                  {columns.map((f) => (
                    <td key={f.fieldname}>
                      <FieldInput
                        field={f}
                        id={`${field.fieldname}-${index}-${f.fieldname}`}
                        compact // the column heading is the label
                        value={row[f.fieldname]}
                        titles={titles}
                        disabled={disabled}
                        onChange={(value) => change(index, f.fieldname, value as string | boolean)}
                      />
                    </td>
                  ))}
                  {!disabled && (
                    <td className="grid-actions">
                      <button
                        type="button"
                        className="icon"
                        aria-label={`Move row ${index + 1} up`}
                        disabled={index === 0}
                        onClick={() => move(index, -1)}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className="icon"
                        aria-label={`Move row ${index + 1} down`}
                        disabled={index === rows.length - 1}
                        onClick={() => move(index, 1)}
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className="icon"
                        aria-label={`Remove row ${index + 1}`}
                        onClick={() => onChange(rows.filter((_, i) => i !== index))}
                      >
                        ×
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length === 0 && <p className="muted small">No rows yet.</p>}
      {!disabled && (
        <button type="button" className="add-row" onClick={() => onChange([...rows, emptyRow(child)])}>
          + Add row
        </button>
      )}
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
