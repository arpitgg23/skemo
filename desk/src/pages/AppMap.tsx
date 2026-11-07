import {
  Background,
  Controls,
  type Edge,
  Handle,
  MarkerType,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo } from "react";
import { Link, useNavigate } from "react-router";
import type { DocType } from "../api";
import { Banner } from "../components/Feedback";
import { usePageTitle } from "../hooks";
import { dataFields, groupByModule, useDocTypes } from "../meta";
import { typeLabel } from "../builder/model";

type DocTypeNode = Node<{ doctype: DocType }, "doctype">;
type ModuleNode = Node<{ label: string }, "module">;

const SHOWN_FIELDS = 6;
const COLUMN_WIDTH = 290;
const nodeTypes = { doctype: DocTypeCard, module: ModuleTitle };

/** The whole app at a glance: DocTypes grouped by module, with a line for every Link. */
export function AppMap() {
  usePageTitle("Builder");
  const navigate = useNavigate();
  const { data, error } = useDocTypes();
  const { nodes, edges } = useMemo(() => layout(data?.doctypes ?? []), [data]);

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>Builder</h1>
          <p className="muted">
            Your app's structure: every DocType, grouped by module. Arrows are Link fields. Click a
            DocType to edit its form.
          </p>
        </div>
        <Link className="button-link" to="/build/new">
          New DocType
        </Link>
      </div>
      {error && <Banner>{error.message}</Banner>}
      {data && data.doctypes.length === 0 ? (
        <div className="panel empty">
          <p>No DocTypes yet.</p>
          <Link className="button-link" to="/build/new">
            Design the first one
          </Link>
        </div>
      ) : (
        <div className="app-map">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            colorMode="system"
            fitView
            nodesConnectable={false}
            onNodeClick={(_, node) => {
              if (node.type === "doctype") navigate(`/build/${node.id}`);
            }}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      )}
    </div>
  );
}

/** Modules side by side, each with its DocTypes stacked underneath. */
function layout(doctypes: DocType[]): { nodes: (DocTypeNode | ModuleNode)[]; edges: Edge[] } {
  const nodes: (DocTypeNode | ModuleNode)[] = [];
  groupByModule(doctypes).forEach(([module, items], column) => {
    const x = column * COLUMN_WIDTH;
    nodes.push({ id: `module:${module}`, type: "module", position: { x, y: 0 }, data: { label: module }, draggable: false, selectable: false });
    let y = 40;
    for (const doctype of items) {
      nodes.push({ id: doctype.name, type: "doctype", position: { x, y }, data: { doctype } });
      y += 64 + 20 * Math.min(dataFields(doctype).length, SHOWN_FIELDS + 1) + 32;
    }
  });
  const known = new Set(doctypes.map((dt) => dt.name));
  const edges: Edge[] = doctypes.flatMap((doctype) =>
    dataFields(doctype)
      .filter((f) => f.fieldtype === "Link" && f.options && f.options !== doctype.name && known.has(f.options))
      .map((f) => ({
        id: `${doctype.name}.${f.fieldname}`,
        source: doctype.name,
        target: f.options ?? "",
        label: f.label,
        markerEnd: { type: MarkerType.ArrowClosed },
      })),
  );
  return { nodes, edges };
}

function DocTypeCard({ data }: NodeProps<DocTypeNode>) {
  const fields = dataFields(data.doctype);
  return (
    <div className="map-node">
      <Handle type="target" position={Position.Left} />
      <strong>{data.doctype.label}</strong>
      <ul>
        {fields.slice(0, SHOWN_FIELDS).map((f) => (
          <li key={f.fieldname}>
            <span>
              {f.label}
              {f.reqd && <span className="required"> *</span>}
            </span>
            <span className="muted">{f.fieldtype === "Link" ? `→ ${f.options}` : typeLabel(f.fieldtype)}</span>
          </li>
        ))}
        {fields.length > SHOWN_FIELDS && <li className="muted">+{fields.length - SHOWN_FIELDS} more</li>}
        {fields.length === 0 && <li className="muted">No fields yet</li>}
      </ul>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

function ModuleTitle({ data }: NodeProps<ModuleNode>) {
  return <div className="map-module">{data.label}</div>;
}
