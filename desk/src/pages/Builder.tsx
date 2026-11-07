import {
  closestCorners,
  DndContext,
  type DragEndEvent,
  type DragOverEvent,
  DragOverlay,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useReducer, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { ApiError, api, type DocType, type FieldType, type SchemaChange } from "../api";
import { Canvas } from "../builder/Canvas";
import {
  type Action,
  allFields,
  type Draft,
  draftProblems,
  fingerprint,
  locate,
  newKey,
  reduce,
  type SavePlan,
  type Selection,
  toDraft,
  toSave,
  typeLabel,
} from "../builder/model";
import { type DragData, Palette } from "../builder/Palette";
import { Preview } from "../builder/Preview";
import { Properties } from "../builder/Properties";
import { Banner, NotFound } from "../components/Feedback";
import { usePageTitle, useSaveShortcut, useUnsavedChangesGuard } from "../hooks";
import { useDocType, useDocTypes } from "../meta";
import { useSession } from "../session";
import { sentence } from "../values";

export function BuilderPage() {
  const { doctype: name } = useParams(); // undefined on /build/new
  const { doctype, isLoading } = useDocType(name);
  if (name && isLoading) return <p className="muted">Loading…</p>;
  if (name && !doctype) return <NotFound what={`DocType "${name}"`} />;
  return <DocTypeBuilder key={doctype?.name ?? "new"} doctype={doctype} />;
}

type Outcome =
  | ({ kind: "saved" } & SchemaChange)
  | { kind: "error"; message: string; details?: string[] };

function DocTypeBuilder({ doctype }: { doctype?: DocType }) {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const { developerMode } = useSession();
  const { data } = useDocTypes();
  // A DocType an app ships is read-only here: its file is the truth. Developer mode lets
  // you edit it anyway, and saving writes the file back for you to commit.
  const fromApp = doctype?.app ?? null;
  const locked = Boolean(fromApp) && !developerMode;
  const known = useMemo(() => (data?.doctypes ?? []).map((dt) => dt.name), [data]);

  const [draft, dispatch] = useReducer(reduce, doctype, toDraft);
  const [baseline, setBaseline] = useState(() => fingerprint(toSave(draft)));
  const [selected, setSelected] = useState<Selection>({ kind: "doctype" });
  const [view, setView] = useState<"build" | "preview">("build");
  // A brand-new DocType arrives here right after its first save, with the result to show.
  const [outcome, setOutcome] = useState<Outcome | null>(() => {
    const saved = (location.state as { saved?: SchemaChange } | null)?.saved;
    return saved ? { kind: "saved", ...saved } : null;
  });
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [dragLabel, setDragLabel] = useState<string | null>(null);
  const leaving = useRef(false);

  const plan = toSave(draft);
  const dirty = fingerprint(plan) !== baseline;
  const problems = draftProblems(draft, known);
  const errors = { ...serverErrors, ...problems };
  usePageTitle(draft.isNew ? "New DocType" : `Build ${draft.label || draft.name}`);
  useUnsavedChangesGuard(dirty, leaving);

  /** Every edit goes through here: it clears the last save's result and server errors. */
  function update(action: Action) {
    dispatch(action);
    setServerErrors({});
    setOutcome(null);
  }

  const save = useMutation({
    mutationFn: (p: SavePlan) =>
      api.saveDocType(p.definition.name, { doctype: p.definition, renames: p.renames, create: draft.isNew }),
    onSuccess: (result, p) => {
      for (const key of [["doctypes"], ["schema-log"], ["list", p.definition.name]]) {
        void queryClient.invalidateQueries({ queryKey: key });
      }
      const change: SchemaChange = { statements: result.statements, archived_rows: result.archived_rows };
      if (draft.isNew) {
        leaving.current = true;
        navigate(`/build/${p.definition.name}`, { replace: true, state: { saved: change } });
        return;
      }
      // Start again from what was saved: renamed fields now have their new names as originals.
      const fresh = toDraft(result.doctype);
      dispatch({ type: "reset", draft: fresh });
      setBaseline(fingerprint(toSave(fresh)));
      setSelected(reselect(selected, draft, fresh));
      setOutcome({ kind: "saved", ...change });
    },
    onError: (error, p) => {
      const issues = (error instanceof ApiError && (error.body as { errors?: unknown })?.errors) || null;
      if (error instanceof ApiError && error.status === 422 && Array.isArray(issues)) {
        // The server points at fields by their index in the flattened list.
        const byKey: Record<string, string> = {};
        const elsewhere: string[] = [];
        for (const issue of issues as { loc: (string | number)[]; message: string }[]) {
          const index = issue.loc[0] === "fields" && typeof issue.loc[1] === "number" ? issue.loc[1] : -1;
          const key = p.keys[index];
          if (key) byKey[key] = sentence(issue.message);
          else elsewhere.push(sentence(issue.message));
        }
        setServerErrors(byKey);
        setOutcome({ kind: "error", message: "The server found problems. They're marked below.", details: elsewhere });
      } else {
        setOutcome({ kind: "error", message: error.message });
      }
    },
  });

  const remove = useMutation({
    mutationFn: () => api.deleteDocType(draft.name),
    onSuccess: () => {
      leaving.current = true;
      void queryClient.invalidateQueries({ queryKey: ["doctypes"] });
      navigate("/build");
    },
    onError: (error) => setOutcome({ kind: "error", message: error.message }),
  });

  function submit() {
    if (!dirty || save.isPending || locked) return;
    if (Object.keys(problems).length > 0) {
      setOutcome({ kind: "error", message: "Fix the problems marked in red first." });
      return;
    }
    save.mutate(plan);
  }
  useSaveShortcut(submit);

  function confirmDelete() {
    const message = `Delete ${draft.label || draft.name}? Its table is dropped; the rows are copied to the archive first.`;
    if (window.confirm(message)) remove.mutate();
  }

  function addField(fieldtype: FieldType, columnKey?: string, index?: number) {
    const key = newKey();
    const target = columnKey ? { columnKey, index } : defaultTarget();
    update({ type: "addField", key, fieldtype, ...target });
    setSelected({ kind: "field", key });
  }

  /** A clicked palette item lands after the selected field, in the selected section, or at
   * the end of the form. */
  function defaultTarget(): { columnKey: string; index?: number } {
    if (selected.kind === "field") {
      const at = locate(draft, selected.key);
      if (at) return { columnKey: at.columnKey, index: at.index + 1 };
    }
    const section =
      (selected.kind === "section" && draft.sections.find((s) => s.key === selected.key)) ||
      draft.sections[draft.sections.length - 1];
    return { columnKey: section.columns[section.columns.length - 1].key };
  }

  function addSection() {
    const key = newKey();
    const afterKey = selected.kind === "section" ? selected.key : undefined;
    update({ type: "addSection", key, afterKey });
    setSelected({ kind: "section", key });
  }

  const sensors = useSensors(
    // A small distance before a drag starts, so clicking a card still selects it.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function onDragStart({ active }: DragStartEvent) {
    const source = active.data.current as DragData | undefined;
    setDragLabel(
      source?.kind === "palette"
        ? typeLabel(source.fieldtype)
        : (allFields(draft).find((f) => f.key === active.id)?.label ?? ""),
    );
  }

  function onDragOver({ active, over }: DragOverEvent) {
    // A field dragged into another column moves there straight away, so the columns make
    // room for it while it's still being dragged.
    if (!over || (active.data.current as DragData | undefined)?.kind !== "field") return;
    const from = locate(draft, String(active.id));
    const to = locate(draft, String(over.id));
    if (from && to && from.columnKey !== to.columnKey) {
      update({ type: "moveField", key: String(active.id), ...to });
    }
  }

  function onDragEnd({ active, over }: DragEndEvent) {
    setDragLabel(null);
    const to = over ? locate(draft, String(over.id)) : undefined;
    if (!to) return;
    const source = active.data.current as DragData | undefined;
    if (source?.kind === "palette") {
      addField(source.fieldtype, to.columnKey, to.index);
      return;
    }
    const from = locate(draft, String(active.id));
    if (from && from.columnKey === to.columnKey && from.index !== to.index) {
      update({ type: "moveField", key: String(active.id), ...to });
    }
  }

  return (
    <div className="builder-page">
      <div className="page-header">
        <div>
          <Link className="crumb" to="/build">
            Builder
          </Link>
          <h1>{draft.isNew ? "New DocType" : draft.label || draft.name}</h1>
          {!draft.isNew && (
            <p className="muted">
              <code>tab_{draft.name}</code> · <Link to={`/app/${draft.name}`}>Open its list</Link>
            </p>
          )}
        </div>
        <div className="actions">
          <div className="tabs" role="group" aria-label="View">
            <button type="button" aria-pressed={view === "build"} onClick={() => setView("build")}>
              Build
            </button>
            <button type="button" aria-pressed={view === "preview"} onClick={() => setView("preview")}>
              Preview
            </button>
          </div>
          {fromApp && <span className="badge">from {fromApp}</span>}
          {dirty && !draft.isNew && !locked && <span className="badge badge-warning">Not saved</span>}
          <button
            type="button"
            className="primary"
            onClick={submit}
            disabled={!dirty || save.isPending || locked}
            title={locked ? `${draft.name} belongs to the app ${fromApp}` : "Save (Ctrl+S)"}
          >
            {save.isPending ? "Saving…" : draft.isNew ? "Create DocType" : "Save"}
          </button>
        </div>
      </div>

      {locked && (
        <Banner kind="warning">
          {draft.label || draft.name} comes from the app <code>{fromApp}</code>, so its file is
          the truth. Edit <code>{`${fromApp}/doctypes/${draft.name}.json`}</code> and run{" "}
          <code>dhancha migrate</code>, or start the server with{" "}
          <code>DHANCHA_DEVELOPER_MODE=1</code> to edit it here and write the file. You can
          still look around and use Preview.
        </Banner>
      )}
      {fromApp && developerMode && (
        <Banner kind="warning">
          Developer mode: saving also writes <code>{`${fromApp}/doctypes/${draft.name}.json`}</code>{" "}
          for you to commit.
        </Banner>
      )}
      {outcome && <OutcomeBanner outcome={outcome} />}

      {view === "preview" ? (
        <Preview definition={plan.definition} />
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={onDragStart}
          onDragOver={onDragOver}
          onDragEnd={onDragEnd}
          onDragCancel={() => setDragLabel(null)}
        >
          <div className="builder">
            <Palette onAdd={(fieldtype) => addField(fieldtype)} onAddSection={addSection} />
            <Canvas
              draft={draft}
              selected={selected}
              errors={errors}
              onSelect={setSelected}
              onAddSection={addSection}
              dispatch={update}
            />
            <Properties
              draft={draft}
              selected={selected}
              errors={errors}
              doctypes={data?.doctypes ?? []}
              dispatch={update}
              onSelect={setSelected}
              onDelete={confirmDelete}
            />
          </div>
          <DragOverlay>{dragLabel !== null ? <div className="drag-overlay">{dragLabel}</div> : null}</DragOverlay>
        </DndContext>
      )}
    </div>
  );
}

function OutcomeBanner({ outcome }: { outcome: Outcome }) {
  if (outcome.kind === "error") {
    return (
      <Banner>
        {sentence(outcome.message)}
        {outcome.details && outcome.details.length > 0 && (
          <ul>
            {outcome.details.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
        )}
      </Banner>
    );
  }
  const count = outcome.statements.length;
  return (
    <Banner kind="success">
      {count === 0
        ? "Saved. The table already matched, so nothing in the database changed."
        : `Saved. ${count} schema ${count === 1 ? "change" : "changes"} applied:`}
      {count > 0 && (
        <ul className="ddl">
          {outcome.statements.map((statement, i) => (
            <li key={i}>
              <code>{statement.replace(/\s+/g, " ")}</code>
            </li>
          ))}
        </ul>
      )}
      {outcome.archived_rows > 0 && (
        <p>
          {outcome.archived_rows} {outcome.archived_rows === 1 ? "value" : "values"} from removed fields
          were copied to the archive first.
        </p>
      )}
      {outcome.written_to && (
        <p>
          Written to <code>{outcome.written_to}</code> — commit it to share the change.
        </p>
      )}
    </Banner>
  );
}

/** Keep the same field selected after a save rebuilt the draft (keys change, names don't). */
function reselect(selected: Selection, before: Draft, after: Draft): Selection {
  if (selected.kind !== "field") return { kind: "doctype" };
  const fieldname = allFields(before).find((f) => f.key === selected.key)?.fieldname;
  const match = allFields(after).find((f) => f.fieldname === fieldname);
  return match ? { kind: "field", key: match.key } : { kind: "doctype" };
}
