import type { CSSProperties } from "react";
import type { Field } from "../api";
import { formLayout } from "../meta";
import type { FormValue, FormValues } from "../values";
import { FieldInput } from "./FieldInput";

type Props = {
  fields: Field[];
  values: FormValues;
  errors?: Record<string, string>;
  onChange: (fieldname: string, value: FormValue) => void;
};

/** A DocType's inputs, laid out in its sections and columns. Used by the record form and
 * by the builder's live preview, so the preview is exactly what people will see. */
export function FormFields({ fields, values, errors = {}, onChange }: Props) {
  return (
    <>
      {formLayout(fields).map((section, i) => (
        <section className="form-section" key={i}>
          {section.label && <h2>{section.label}</h2>}
          <div className="form-columns" style={{ "--columns": section.columns.length } as CSSProperties}>
            {section.columns.map((column, j) => (
              <div key={j}>
                {column.map((field) => (
                  <FieldInput
                    key={field.fieldname}
                    field={field}
                    value={values[field.fieldname]}
                    error={errors[field.fieldname]}
                    onChange={(value) => onChange(field.fieldname, value)}
                  />
                ))}
              </div>
            ))}
          </div>
        </section>
      ))}
    </>
  );
}
