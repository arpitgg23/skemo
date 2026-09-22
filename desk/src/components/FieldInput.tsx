import type { ChangeEvent, ReactNode } from "react";
import type { DataField } from "../api";
import { choices } from "../meta";
import { fieldMessage, type FormValue } from "../values";
import { LinkInput } from "./LinkInput";

type Props = {
  field: DataField;
  value: FormValue | undefined;
  error?: string;
  onChange: (value: FormValue) => void;
};

/** The input for one field, picked by its fieldtype, with its label, help and error. */
export function FieldInput({ field, value, error, onChange }: Props) {
  const id = `field-${field.fieldname}`;
  const label = field.label || field.fieldname;
  const describedBy =
    [field.description && `${id}-help`, error && `${id}-error`].filter(Boolean).join(" ") ||
    undefined;
  const common = {
    id,
    disabled: field.read_only,
    "aria-invalid": error ? true : undefined,
    "aria-required": field.reqd || undefined,
    "aria-describedby": describedBy,
  };
  const text = typeof value === "string" ? value : "";
  const onText = (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    onChange(event.target.value);

  const notes = (
    <>
      {field.description && (
        <p id={`${id}-help`} className="help">
          {field.description}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="error-text">
          {fieldMessage(label, error)}
        </p>
      )}
    </>
  );

  if (field.fieldtype === "Check") {
    return (
      <div className={`field field-check${error ? " has-error" : ""}`}>
        <label>
          <input
            type="checkbox"
            {...common}
            checked={value === true}
            onChange={(event) => onChange(event.target.checked)}
          />
          {label}
        </label>
        {notes}
      </div>
    );
  }

  let control: ReactNode;
  switch (field.fieldtype) {
    case "Text":
      control = <textarea rows={4} {...common} value={text} onChange={onText} />;
      break;
    case "Int":
      control = <input type="number" step={1} inputMode="numeric" {...common} value={text} onChange={onText} />;
      break;
    case "Float":
      control = <input type="number" step="any" inputMode="decimal" {...common} value={text} onChange={onText} />;
      break;
    case "Currency":
      control = <input type="number" step="0.01" inputMode="decimal" {...common} value={text} onChange={onText} />;
      break;
    case "Date":
      control = <input type="date" {...common} value={text} onChange={onText} />;
      break;
    case "Datetime":
      control = <input type="datetime-local" {...common} value={text} onChange={onText} />;
      break;
    case "Select":
      control = (
        <select {...common} value={text} onChange={onText}>
          <option value="">{field.reqd ? "Choose…" : "—"}</option>
          {choices(field).map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
      );
      break;
    case "Link":
      control = <LinkInput {...common} target={field.options ?? ""} value={text} onChange={onChange} />;
      break;
    default:
      control = (
        <input type="text" maxLength={field.length ?? 140} {...common} value={text} onChange={onText} />
      );
  }

  return (
    <div className={`field${error ? " has-error" : ""}`}>
      <label htmlFor={id}>
        {label}
        {field.reqd && (
          <span className="required" aria-hidden="true">
            {" *"}
          </span>
        )}
      </label>
      {control}
      {notes}
    </div>
  );
}
