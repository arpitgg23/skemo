import { useDraggable } from "@dnd-kit/core";
import type { FieldType } from "../api";
import { PALETTE } from "./model";

/** What's being dragged: a new field from the palette, or a field already on the form. */
export type DragData = { kind: "palette"; fieldtype: FieldType } | { kind: "field" };

type Props = { onAdd: (fieldtype: FieldType) => void; onAddSection: () => void };

export function Palette({ onAdd, onAddSection }: Props) {
  return (
    <aside className="palette" aria-label="Field types">
      <p className="palette-title">Drag onto the form, or click to add</p>
      {PALETTE.map((item) => (
        <PaletteItem key={item.fieldtype} {...item} onAdd={onAdd} />
      ))}
      <button
        type="button"
        className="palette-item palette-layout"
        aria-label="Add section"
        onClick={onAddSection}
      >
        <span>+ Section</span>
        <span className="muted small">a new block with a heading</span>
      </button>
    </aside>
  );
}

function PaletteItem({
  fieldtype,
  label,
  onAdd,
}: {
  fieldtype: FieldType;
  label: string;
  onAdd: (fieldtype: FieldType) => void;
}) {
  const data: DragData = { kind: "palette", fieldtype };
  const { attributes, listeners, setNodeRef } = useDraggable({ id: `palette:${fieldtype}`, data });
  // Keyboard users add with Enter or Space (a click), so leave out the keyboard-drag listener.
  const { onKeyDown: _, ...pointer } = listeners ?? {};
  return (
    <button
      ref={setNodeRef}
      type="button"
      className="palette-item"
      aria-label={`Add ${label} field`}
      {...attributes}
      {...pointer}
      onClick={() => onAdd(fieldtype)}
    >
      <span>{label}</span>
      <span className="muted small">{fieldtype}</span>
    </button>
  );
}
