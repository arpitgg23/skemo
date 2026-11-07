import { useDroppable } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { CSSProperties } from "react";
import type { Action, BuilderField, Column, Draft, Section, Selection } from "./model";
import { typeLabel } from "./model";
import type { DragData } from "./Palette";

type Props = {
  draft: Draft;
  selected: Selection;
  errors: Record<string, string>;
  onSelect: (selection: Selection) => void;
  onAddSection: () => void;
  dispatch: (action: Action) => void;
};

/** The form as editable blocks: sections, their columns, and field cards to drag around. */
export function Canvas({ draft, selected, errors, onSelect, onAddSection, dispatch }: Props) {
  return (
    <div className="canvas">
      {errors.doctype && <p className="error-text canvas-problem">{errors.doctype}</p>}
      {draft.sections.map((section) => (
        <SectionBlock
          key={section.key}
          section={section}
          selected={selected}
          errors={errors}
          onSelect={onSelect}
          dispatch={dispatch}
        />
      ))}
      <button type="button" className="add-section" onClick={onAddSection}>
        + Add section
      </button>
    </div>
  );
}

function SectionBlock({
  section,
  selected,
  errors,
  onSelect,
  dispatch,
}: Omit<Props, "draft" | "onAddSection"> & { section: Section }) {
  const isSelected = selected.kind === "section" && selected.key === section.key;
  return (
    <section className={`bsection${isSelected ? " selected" : ""}`}>
      <button
        type="button"
        className="bsection-head"
        aria-label={`Section ${section.label || "(untitled)"}`}
        aria-pressed={isSelected}
        onClick={() => onSelect({ kind: "section", key: section.key })}
      >
        <span className="bsection-title">{section.label || "Untitled section"}</span>
        <span className="muted small">
          {section.columns.length} {section.columns.length === 1 ? "column" : "columns"}
        </span>
      </button>
      <div className="bcolumns" style={{ "--columns": section.columns.length } as CSSProperties}>
        {section.columns.map((column) => (
          <ColumnBlock
            key={column.key}
            column={column}
            selected={selected}
            errors={errors}
            onSelect={onSelect}
            dispatch={dispatch}
          />
        ))}
      </div>
    </section>
  );
}

function ColumnBlock({
  column,
  selected,
  errors,
  onSelect,
  dispatch,
}: Omit<Props, "draft" | "onAddSection"> & { column: Column }) {
  // The column itself is a drop target, so empty columns (and the space below the last
  // field) accept fields too.
  const { setNodeRef, isOver } = useDroppable({ id: column.key, data: { kind: "column" } });
  return (
    <SortableContext id={column.key} items={column.fields.map((f) => f.key)} strategy={verticalListSortingStrategy}>
      <div ref={setNodeRef} className={`bcolumn${isOver ? " over" : ""}`}>
        {column.fields.map((field) => (
          <FieldCard
            key={field.key}
            field={field}
            selected={selected.kind === "field" && selected.key === field.key}
            error={errors[field.key]}
            onSelect={() => onSelect({ kind: "field", key: field.key })}
            onRemove={() => dispatch({ type: "removeField", key: field.key })}
          />
        ))}
        {column.fields.length === 0 && <p className="bcolumn-empty">Drop fields here</p>}
      </div>
    </SortableContext>
  );
}

function FieldCard({
  field,
  selected,
  error,
  onSelect,
  onRemove,
}: {
  field: BuilderField;
  selected: boolean;
  error?: string;
  onSelect: () => void;
  onRemove: () => void;
}) {
  const data: DragData = { kind: "field" };
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id: field.key, data });
  const label = field.label || field.fieldname || typeLabel(field.fieldtype);
  const kind = field.fieldtype === "Link" ? `Link → ${field.options || "?"}` : typeLabel(field.fieldtype);
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`bfield${selected ? " selected" : ""}${error ? " invalid" : ""}${isDragging ? " dragging" : ""}`}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        className="handle"
        aria-label={`Move ${label}`}
        {...attributes}
        {...listeners}
      >
        ⠿
      </button>
      <button
        type="button"
        className="bfield-main"
        aria-label={`Edit ${label}`}
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="bfield-label">
          {label}
          {field.reqd && <span className="required"> *</span>}
          {field.hidden && <span className="muted small"> (hidden)</span>}
        </span>
        <span className="muted small">
          {kind} · <code>{field.fieldname}</code>
        </span>
        {error && <span className="error-text">{error}</span>}
      </button>
      <button type="button" className="icon" aria-label={`Remove ${label}`} onClick={onRemove}>
        ×
      </button>
    </div>
  );
}
