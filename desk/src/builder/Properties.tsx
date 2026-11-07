import { type ReactNode, useId } from "react";
import type { DocType, Field, FieldType } from "../api";
import {
  type Action,
  allFields,
  type BuilderField,
  type Draft,
  findField,
  MAX_COLUMNS,
  PALETTE,
  type Section,
  type Selection,
  splitAutoname,
  typeLabel,
} from "./model";

type Props = {
  draft: Draft;
  selected: Selection;
  errors: Record<string, string>;
  doctypes: DocType[];
  dispatch: (action: Action) => void;
  onSelect: (selection: Selection) => void;
  onDelete: () => void;
};

/** Settings for whatever is selected: a field, a section, or the DocType itself. */
export function Properties(props: Props) {
  const { draft, selected } = props;
  const field = selected.kind === "field" ? findField(draft, selected.key) : undefined;
  const section = selected.kind === "section" ? draft.sections.find((s) => s.key === selected.key) : undefined;
  return (
    <aside className="props" aria-label="Properties">
      {field ? (
        <FieldProperties {...props} field={field} />
      ) : section ? (
        <SectionProperties {...props} section={section} />
      ) : (
        <DocTypeProperties {...props} />
      )}
    </aside>
  );
}

function FieldProperties({ field, errors, doctypes, dispatch, onSelect }: Props & { field: BuilderField }) {
  const set = (changes: Partial<Field>) => dispatch({ type: "updateField", key: field.key, changes });
  const renamed = field.original !== undefined && field.original !== field.fieldname;
  const fieldnameHint = renamed
    ? `Renamed from ${field.original}: the column is renamed and keeps its data.`
    : field.original
      ? "The column's name in the database."
      : "Follows the label until you edit it.";
  const defaultHint: Partial<Record<FieldType, string>> = {
    Date: "A date like 2026-10-01, or Today",
    Datetime: "Or Now",
    Check: "1 to start ticked",
    Select: "One of the choices",
  };
  return (
    <>
      <Header title={`${typeLabel(field.fieldtype)} field`} onBack={() => onSelect({ kind: "doctype" })} />
      {errors[field.key] && <p className="error-text">{errors[field.key]}</p>}
      <TextProp label="Label" value={field.label ?? ""} onChange={(label) => set({ label })} />
      <TextProp label="Fieldname" mono value={field.fieldname ?? ""} hint={fieldnameHint} onChange={(fieldname) => set({ fieldname })} />
      <SelectProp
        label="Type"
        value={field.fieldtype}
        options={PALETTE.map((p) => [p.fieldtype, `${p.label} (${p.fieldtype})`])}
        hint={field.original ? "Existing values are converted. If any can't be, the save is refused and nothing changes." : undefined}
        onChange={(fieldtype) => dispatch({ type: "changeType", key: field.key, fieldtype: fieldtype as FieldType })}
      />
      {field.fieldtype === "Select" && (
        <TextareaProp label="Choices" hint="One per line" value={field.options ?? ""} onChange={(options) => set({ options })} />
      )}
      {field.fieldtype === "Link" && (
        <SelectProp
          label="Links to"
          value={field.options ?? ""}
          options={[["", "Choose a DocType…"], ...doctypes.map((dt): [string, string] => [dt.name, dt.label])]}
          onChange={(options) => set({ options: options || undefined })}
        />
      )}
      {field.fieldtype === "Data" && (
        <TextProp
          label="Max length"
          type="number"
          placeholder="140"
          value={field.length ? String(field.length) : ""}
          onChange={(length) => set({ length: length ? Number(length) : undefined })}
        />
      )}
      <TextProp
        label="Default"
        value={field.default ?? ""}
        hint={defaultHint[field.fieldtype]}
        onChange={(value) => set({ default: value || undefined })}
      />
      <TextProp
        label="Help text"
        value={field.description ?? ""}
        onChange={(description) => set({ description: description || undefined })}
      />
      <fieldset className="checks">
        <legend>Options</legend>
        <CheckProp label="Required" checked={field.reqd} onChange={(reqd) => set({ reqd })} />
        <CheckProp label="Unique" checked={field.unique} onChange={(unique) => set({ unique })} />
        <CheckProp label="Show in list" checked={field.in_list_view} onChange={(in_list_view) => set({ in_list_view })} />
        <CheckProp label="Search index" checked={field.search_index} onChange={(search_index) => set({ search_index })} />
        <CheckProp label="Read only" checked={field.read_only} onChange={(read_only) => set({ read_only })} />
        <CheckProp label="Hidden" checked={field.hidden} onChange={(hidden) => set({ hidden })} />
      </fieldset>
      <button
        type="button"
        className="danger"
        onClick={() => {
          dispatch({ type: "removeField", key: field.key });
          onSelect({ kind: "doctype" });
        }}
      >
        Remove field
      </button>
      {field.original && (
        <p className="help">Saving without this field drops its column. Its values are copied to the archive first.</p>
      )}
    </>
  );
}

function SectionProperties({ draft, section, dispatch, onSelect }: Props & { section: Section }) {
  const index = draft.sections.indexOf(section);
  const lastColumn = section.columns[section.columns.length - 1];
  return (
    <>
      <Header title="Section" onBack={() => onSelect({ kind: "doctype" })} />
      <TextProp
        label="Heading"
        value={section.label}
        hint="Leave empty for no heading"
        onChange={(label) => dispatch({ type: "updateSection", key: section.key, label })}
      />
      <p className="prop-label">
        Columns: {section.columns.length} of up to {MAX_COLUMNS}
      </p>
      <div className="actions">
        <button
          type="button"
          disabled={section.columns.length >= MAX_COLUMNS}
          onClick={() => dispatch({ type: "addColumn", sectionKey: section.key })}
        >
          + Column
        </button>
        <button
          type="button"
          disabled={section.columns.length <= 1}
          onClick={() => dispatch({ type: "removeColumn", sectionKey: section.key, columnKey: lastColumn.key })}
        >
          − Column
        </button>
      </div>
      <div className="actions prop-row">
        <button type="button" disabled={index === 0} onClick={() => dispatch({ type: "moveSection", key: section.key, delta: -1 })}>
          Move up
        </button>
        <button
          type="button"
          disabled={index === draft.sections.length - 1}
          onClick={() => dispatch({ type: "moveSection", key: section.key, delta: 1 })}
        >
          Move down
        </button>
        <button
          type="button"
          className="danger"
          disabled={draft.sections.length === 1}
          onClick={() => {
            dispatch({ type: "removeSection", key: section.key });
            onSelect({ kind: "doctype" });
          }}
        >
          Remove
        </button>
      </div>
      <p className="help">Removing a section or column keeps its fields: they move to the neighbouring one.</p>
    </>
  );
}

function DocTypeProperties({ draft, errors, doctypes, dispatch, onDelete }: Props) {
  const set = (changes: Parameters<typeof doc>[0]) => dispatch(doc(changes));
  const modules = [...new Set(doctypes.map((dt) => dt.module))];
  const listId = useId();
  return (
    <>
      <Header title="DocType" />
      {errors.doctype && <p className="error-text">{errors.doctype}</p>}
      <TextProp label="Label" placeholder="e.g. Sales Invoice" value={draft.label} onChange={(label) => set({ label })} />
      {draft.isNew ? (
        <TextProp
          label="Name"
          mono
          value={draft.name}
          hint={`Its table will be tab_${draft.name || "…"}. It can't be changed later.`}
          onChange={(name) => set({ name })}
        />
      ) : (
        <p className="help">
          Name <code>{draft.name}</code>, table <code>tab_{draft.name}</code>
        </p>
      )}
      <TextProp label="Module" placeholder="Core" list={listId} value={draft.module} onChange={(module) => set({ module })} />
      <datalist id={listId}>
        {modules.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      <NamingProps draft={draft} onChange={(autoname) => set({ autoname })} />
      <TextareaProp label="Description" value={draft.description} onChange={(description) => set({ description })} />
      {!draft.isNew && (
        <>
          <button type="button" className="danger" onClick={onDelete}>
            Delete DocType
          </button>
          <p className="help">Drops its table after archiving the rows. Refused while other DocTypes link to it.</p>
        </>
      )}
    </>
  );
}

const doc = (changes: Extract<Action, { type: "doc" }>["changes"]): Action => ({ type: "doc", changes });

function NamingProps({ draft, onChange }: { draft: Draft; onChange: (autoname: string) => void }) {
  const [kind, arg = ""] = splitAutoname(draft.autoname);
  const textFields = allFields(draft).filter((f) => f.fieldtype === "Data" && f.fieldname);
  const prefix = (draft.name || "doc").slice(0, 4).toUpperCase();
  return (
    <>
      <SelectProp
        label="Record names"
        value={kind}
        options={[
          ["hash", "Random, like 3f9c01b2e7"],
          ["format", "A pattern, like INV-2026-00001"],
          ["field", "Copied from a field"],
          ["prompt", "Typed in when created"],
        ]}
        hint={draft.isNew ? undefined : "Only new records are named this way."}
        onChange={(next) =>
          onChange(
            next === "format"
              ? `format:${prefix}-{#####}`
              : next === "field"
                ? `field:${textFields[0]?.fieldname ?? ""}`
                : next,
          )
        }
      />
      {kind === "format" && (
        <TextProp
          label="Pattern"
          mono
          value={arg}
          hint="{YYYY} {YY} {MM} {DD} are today's date; one counter like {#####}. Each prefix counts on its own and never skips a number."
          onChange={(pattern) => onChange(`format:${pattern}`)}
        />
      )}
      {kind === "field" && (
        <SelectProp
          label="From field"
          value={arg}
          options={[["", "Choose a field…"], ...textFields.map((f): [string, string] => [f.fieldname ?? "", f.label || (f.fieldname ?? "")])]}
          onChange={(fieldname) => onChange(`field:${fieldname}`)}
        />
      )}
    </>
  );
}

function Header({ title, onBack }: { title: string; onBack?: () => void }) {
  return (
    <div className="props-head">
      <h2>{title}</h2>
      {onBack && (
        <button type="button" className="link-button" onClick={onBack}>
          DocType settings
        </button>
      )}
    </div>
  );
}

function Prop({ label, hint, id, children }: { label: string; hint?: string; id: string; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && <p className="help">{hint}</p>}
    </div>
  );
}

function TextProp({
  label,
  value,
  onChange,
  hint,
  mono,
  ...input
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  mono?: boolean;
  type?: string;
  placeholder?: string;
  list?: string;
}) {
  const id = useId();
  return (
    <Prop label={label} hint={hint} id={id}>
      <input id={id} className={mono ? "mono" : undefined} value={value} onChange={(e) => onChange(e.target.value)} {...input} />
    </Prop>
  );
}

function TextareaProp({ label, value, onChange, hint }: { label: string; value: string; onChange: (value: string) => void; hint?: string }) {
  const id = useId();
  return (
    <Prop label={label} hint={hint} id={id}>
      <textarea id={id} rows={4} value={value} onChange={(e) => onChange(e.target.value)} />
    </Prop>
  );
}

function SelectProp({
  label,
  value,
  options,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  options: [string, string][];
  onChange: (value: string) => void;
  hint?: string;
}) {
  const id = useId();
  return (
    <Prop label={label} hint={hint} id={id}>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map(([optionValue, text]) => (
          <option key={optionValue} value={optionValue}>
            {text}
          </option>
        ))}
      </select>
    </Prop>
  );
}

function CheckProp({ label, checked, onChange }: { label: string; checked?: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="check">
      <input type="checkbox" checked={Boolean(checked)} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}
