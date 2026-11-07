import { useState } from "react";
import type { DocType, DocTypeDefinition } from "../api";
import { FormFields } from "../components/FormFields";
import { initialValues, type FormValues } from "../values";

/** The form exactly as people will see it, built from the unsaved draft. Typing into it
 * saves nothing. */
export function Preview({ definition }: { definition: DocTypeDefinition }) {
  const doctype: DocType = {
    label: definition.name,
    module: "Core",
    autoname: "hash",
    ...definition,
    table: `tab_${definition.name}`,
    can: ["read", "write", "create", "delete"], // the preview is always fully editable
    columns: [],
  };
  const [values, setValues] = useState<FormValues>(() => initialValues(doctype));
  const hasFields = definition.fields.some((f) => f.fieldname);
  return (
    <div className="preview">
      <p className="muted">
        This is the form people will fill in. Try it out: nothing here is saved, and required
        fields are marked with *.
      </p>
      {hasFields ? (
        <FormFields
          fields={definition.fields}
          values={values}
          onChange={(fieldname, value) => setValues((current) => ({ ...current, [fieldname]: value }))}
        />
      ) : (
        <p className="panel empty">Add some fields to see the form.</p>
      )}
    </div>
  );
}
