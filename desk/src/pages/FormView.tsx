import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router";
import { ApiError, api, type Doc, type DocType } from "../api";
import { Banner, NotFound } from "../components/Feedback";
import { FormFields } from "../components/FormFields";
import { usePageTitle, useSaveShortcut, useUnsavedChangesGuard } from "../hooks";
import { dataFields, docUrl, titleField, useDocType } from "../meta";
import {
  changedFields,
  type FormValues,
  fieldMessage,
  fromFormValue,
  initialValues,
  missingRequired,
  sentence,
  timeAgo,
  toFormValues,
} from "../values";

export function FormView() {
  const params = useParams();
  const name = params["*"]; // the record's name; undefined on /app/:doctype/new
  const { doctype, isLoading } = useDocType(params.doctype);
  const location = useLocation();
  const [flash, setFlash] = useState<string | null>(
    (location.state as { flash?: string } | null)?.flash ?? null,
  );
  useEffect(() => {
    if (!flash) return;
    const timer = setTimeout(() => setFlash(null), 3000);
    return () => clearTimeout(timer);
  }, [flash]);

  const doc = useQuery({
    queryKey: ["doc", params.doctype, name],
    queryFn: () => api.get(params.doctype ?? "", name ?? ""),
    enabled: Boolean(doctype && name),
    // Never swap a record out from under someone typing: it reloads only after a save or
    // when asked to.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  if (isLoading) return <p className="muted">Loading…</p>;
  if (!doctype) return <NotFound what={`DocType "${params.doctype}"`} />;
  if (name && doc.error) {
    if (doc.error instanceof ApiError && doc.error.status === 404) {
      return <NotFound what={`${doctype.label} "${name}"`} />;
    }
    return <Banner>{doc.error.message}</Banner>;
  }
  if (name && !doc.data) return <p className="muted">Loading…</p>;
  return (
    <DocForm
      // A fresh form for each saved version, so it restarts from what's stored.
      key={`${doctype.name}/${name ?? "new"}/${doc.data?.modified_at ?? ""}`}
      doctype={doctype}
      doc={doc.data}
      flash={flash}
      onSaved={setFlash}
    />
  );
}

type Problem = { message: string; details?: string[]; stale?: boolean };

function DocForm({
  doctype,
  doc,
  flash,
  onSaved,
}: {
  doctype: DocType;
  doc?: Doc; // undefined for a new record
  flash: string | null;
  onSaved: (message: string) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const fields = useMemo(() => new Map(dataFields(doctype).map((f) => [f.fieldname, f])), [doctype]);
  const original = useMemo(
    () => (doc ? toFormValues(doctype, doc) : initialValues(doctype)),
    [doctype, doc],
  );
  const [values, setValues] = useState<FormValues>(original);
  const [newName, setNewName] = useState(""); // only for autoname "prompt"
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<Problem | null>(null);
  const leaving = useRef(false); // set right before navigating away after a save or delete
  const askName = !doc && doctype.autoname === "prompt";
  const changed = changedFields(doctype, original, values);
  const dirty = changed.length > 0 || newName.trim() !== "";
  const title = titleField(doctype);
  usePageTitle(doc ? doc.name : `New ${doctype.label}`);

  const save = useMutation({
    mutationFn: () => {
      if (doc) {
        const payload = Object.fromEntries(
          changed.map((n) => [n, fromFormValue(fields.get(n)!, values[n])]),
        );
        // modified_at lets the server refuse the save if someone else changed it first.
        return api.update(doctype.name, doc.name, { ...payload, modified_at: doc.modified_at });
      }
      const payload: Record<string, unknown> = {};
      for (const [fieldname, field] of fields) {
        const value = fromFormValue(field, values[fieldname]);
        if (value !== null) payload[fieldname] = value;
      }
      if (askName) payload.name = newName.trim();
      return api.create(doctype.name, payload);
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(["doc", doctype.name, saved.name], saved);
      void queryClient.invalidateQueries({ queryKey: ["list", doctype.name] });
      if (doc) {
        onSaved("Saved");
        return;
      }
      // FormView may be reused for the new URL or mounted afresh; the flash covers both.
      const flash = `Created ${saved.name}`;
      onSaved(flash);
      leaving.current = true;
      navigate(docUrl(doctype.name, saved.name), { replace: true, state: { flash } });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 422) {
        setErrors(Object.fromEntries(error.errors.map((e) => [e.field, e.message])));
        // Problems with no input on this form are listed in the banner instead.
        const onForm = (field: string) => fields.has(field) || (askName && field === "name");
        const elsewhere = error.errors
          .filter((e) => !onForm(e.field))
          .map((e) => fieldMessage(e.field, e.message));
        setProblem({ message: "Some fields need fixing.", details: elsewhere });
      } else {
        const stale = error instanceof ApiError && error.code === "stale";
        setProblem({ message: error.message, stale });
      }
    },
  });

  const remove = useMutation({
    mutationFn: () => api.remove(doctype.name, doc?.name ?? ""),
    onSuccess: () => {
      leaving.current = true;
      void queryClient.invalidateQueries({ queryKey: ["list", doctype.name] });
      navigate(`/app/${doctype.name}`);
      queryClient.removeQueries({ queryKey: ["doc", doctype.name, doc?.name] });
    },
    onError: (error) => setProblem({ message: error.message }),
  });

  function submit() {
    if (!dirty || save.isPending) return;
    const missing = missingRequired(doctype, values);
    if (askName && !newName.trim()) missing.name = "is required";
    setErrors(missing);
    if (Object.keys(missing).length > 0) {
      setProblem({ message: "Some fields need fixing." });
      return;
    }
    setProblem(null);
    save.mutate();
  }

  function setValue(fieldname: string, value: FormValues[string]) {
    setValues((current) => ({ ...current, [fieldname]: value }));
    setErrors(({ [fieldname]: _, ...rest }) => rest);
  }

  function confirmDelete() {
    if (doc && window.confirm(`Delete ${doc.name}? A copy is kept in the archive.`)) remove.mutate();
  }

  useSaveShortcut(submit);
  useUnsavedChangesGuard(dirty, leaving);

  return (
    <div className="form-page">
      <div className="page-header">
        <div>
          <Link className="crumb" to={`/app/${doctype.name}`}>
            {doctype.label}
          </Link>
          <h1>{doc ? doc.name : `New ${doctype.label}`}</h1>
          {doc && title && doc[title.fieldname] ? (
            <p className="muted">{String(doc[title.fieldname])}</p>
          ) : null}
        </div>
        <div className="actions">
          {flash && (
            <span className="badge badge-success" role="status">
              {flash}
            </span>
          )}
          {doc && dirty && <span className="badge badge-warning">Not saved</span>}
          {doc && (
            <button type="button" className="danger" onClick={confirmDelete} disabled={remove.isPending}>
              Delete
            </button>
          )}
          <button
            type="button"
            className="primary"
            onClick={submit}
            disabled={!dirty || save.isPending}
            title="Save (Ctrl+S)"
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {problem && (
        <Banner kind={problem.stale ? "warning" : "error"}>
          {sentence(problem.message)}
          {problem.details && problem.details.length > 0 && (
            <ul>
              {problem.details.map((detail) => (
                <li key={detail}>{detail}</li>
              ))}
            </ul>
          )}
          {problem.stale && doc && (
            <div>
              <button
                type="button"
                onClick={() =>
                  void queryClient.invalidateQueries({ queryKey: ["doc", doctype.name, doc.name] })
                }
              >
                Reload (discards your changes)
              </button>
            </div>
          )}
        </Banner>
      )}

      {askName && (
        <section className="form-section">
          <div className={`field${errors.name ? " has-error" : ""}`}>
            <label htmlFor="field-name">
              Name<span className="required"> *</span>
            </label>
            <input
              id="field-name"
              maxLength={140}
              placeholder="e.g. INV/24-25/001"
              value={newName}
              onChange={(event) => {
                setNewName(event.target.value);
                setErrors(({ name: _, ...rest }) => rest);
              }}
            />
            {errors.name && <p className="error-text">{fieldMessage("Name", errors.name)}</p>}
          </div>
        </section>
      )}

      <FormFields fields={doctype.fields} values={values} errors={errors} onChange={setValue} />

      {doc && (
        <p className="form-meta muted">
          Created by {doc.owner} {timeAgo(doc.created_at)} · last changed by {doc.modified_by}{" "}
          {timeAgo(doc.modified_at)}
        </p>
      )}
    </div>
  );
}
