import type { CSSProperties } from "react";
import type { Field, LinkTitles } from "../api";
import { formLayout, isGrid } from "../meta";
import { asRows, type Children, type FormValue, type FormValues } from "../values";
import { FieldInput } from "./FieldInput";
import { GridInput } from "./GridInput";

type Props = {
  fields: Field[];
  values: FormValues;
  errors?: Record<string, string>;
  onChange: (fieldname: string, value: FormValue) => void;
  /** Needed to draw a Table field's grid. */
  children?: Children;
  /** Titles behind the record's Link values. The builder's preview has none. */
  titles?: LinkTitles;
  disabled?: boolean;
};

/** A DocType's inputs, laid out in its sections and columns. Used by the record form and
 * by the builder's live preview, so the preview is exactly what people will see. */
export function FormFields({
  fields,
  values,
  errors = {},
  onChange,
  children,
  titles,
  disabled,
}: Props) {
  return (
    <>
      {formLayout(fields).map((section, i) => (
        <section className="form-section" key={i}>
          {section.label && <h2>{section.label}</h2>}
          <div className="form-columns" style={{ "--columns": section.columns.length } as CSSProperties}>
            {section.columns.map((column, j) => (
              <div key={j}>
                {column.map((field) =>
                  isGrid(field) ? (
                    <GridInput
                      key={field.fieldname}
                      field={field}
                      child={children?.(field.options ?? "")}
                      rows={asRows(values[field.fieldname])}
                      error={errors[field.fieldname]}
                      titles={titles}
                      disabled={disabled || field.read_only}
                      onChange={(rows) => onChange(field.fieldname, rows)}
                    />
                  ) : (
                    <FieldInput
                      key={field.fieldname}
                      field={field}
                      value={values[field.fieldname]}
                      error={errors[field.fieldname]}
                      titles={titles}
                      disabled={disabled}
                      onChange={(value) => onChange(field.fieldname, value)}
                    />
                  ),
                )}
              </div>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
