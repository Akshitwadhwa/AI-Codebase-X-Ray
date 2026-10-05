const ROLE_LABEL = {
  focus: "selected",
  caller: "caller",
  callee: "callee",
  class: "class",
  file: "file",
  test: "test",
  import: "import",
  base: "base",
};

const LANE = {
  caller: 0,
  test: 0,
  class: 1,
  base: 1,
  focus: 1,
  file: 1,
  callee: 2,
  import: 2,
};

const LANE_ORDER = { caller: 0, test: 1, class: 0, base: 1, focus: 2, file: 3, callee: 0, import: 1 };
const EDGE_COLOR = { CALLS: "#7eb8ff", IMPORTS: "#a9ef5c", INHERITS: "#f3f6fb", CONTAINS: "#516075" };
const STEP = 104;
const COLUMN = 320;

let flowLibs = null;
let reactRoot = null;

function shortPath(path) {
  if (!path || path.length <= 34) return path || "";
  const tail = path.split("/").slice(-2).join("/");
  return tail.length < path.length ? `…/${tail}` : path;
}

function roleLabel(node) {
  const name = ROLE_LABEL[node.role] || node.kind || "node";
  return node.depth > 1 ? `${name} · depth ${node.depth}` : name;
}

function nodeData(node) {
  const symbol = node.kind === "class" || node.kind === "function" || node.kind === "method";
  const path = node.file_path && node.file_path !== node.label ? shortPath(node.file_path) : "";
  return {
    label: node.label,
    caption: symbol || node.kind === "file" ? path : node.kind === "folder" ? shortPath(node.file_path || node.label) : "",
    role: node.role || node.kind || "file",
    roleLabel: roleLabel(node),
  };
}

function placed(node, x, y) {
  return {
    id: node.id,
    type: "symbol",
    position: { x, y },
    data: nodeData(node),
    style: { width: 230, padding: 0, border: "none", background: "transparent", boxShadow: "none" },
  };
}

function layoutNeighborhood(nodes) {
  const lanes = [[], [], []];
  nodes.forEach((node) => {
    const lane = LANE[node.role];
    (lane === undefined ? lanes[1] : lanes[lane]).push(node);
  });
  lanes.forEach((group) => {
    group.sort((left, right) => (LANE_ORDER[left.role] ?? 9) - (LANE_ORDER[right.role] ?? 9) || left.label.localeCompare(right.label));
  });
  const boxes = [];
  lanes.forEach((group, lane) => {
    const span = group.length * STEP;
    const offset = -span / 2;
    group.forEach((node, index) => boxes.push(placed(node, lane * COLUMN, offset + index * STEP)));
  });
  return boxes;
}

function layoutArchitecture(nodes) {
  const repos = [];
  const folders = [];
  const files = [];
  nodes.forEach((node) => {
    if (node.kind === "repository" || node.role === "repository") repos.push(node);
    else if (node.kind === "folder" || node.role === "folder") folders.push(node);
    else files.push(node);
  });
  const boxes = [];
  const row = (group, y, columns) => {
    group.forEach((node, index) => {
      boxes.push(placed(node, (index % columns) * 250, y + Math.floor(index / columns) * STEP));
    });
  };
  row(repos, 0, 1);
  row(folders, 120, 4);
  const folderRows = Math.max(1, Math.ceil(folders.length / 4));
  row(files, 140 + folderRows * STEP, 4);
  return boxes;
}

function layout(view) {
  return view.view === "neighborhood" ? layoutNeighborhood(view.nodes) : layoutArchitecture(view.nodes);
}

async function libs() {
  if (flowLibs) return flowLibs;
  const React = (await import("https://esm.sh/react@18.3.1")).default;
  const { createRoot } = await import("https://esm.sh/react-dom@18.3.1/client");
  const flow = await import("https://esm.sh/reactflow@11.11.4?deps=react@18.3.1,react-dom@18.3.1");
  const { Handle, Position } = flow;

  function SymbolNode({ data }) {
    return React.createElement(
      "div",
      { className: `xray-node role-${data.role}` },
      React.createElement(Handle, { type: "target", position: Position.Left, id: "left" }),
      React.createElement(Handle, { type: "target", position: Position.Top, id: "top" }),
      React.createElement(Handle, { type: "source", position: Position.Top, id: "top-out" }),
      React.createElement("div", { className: "xray-role" }, data.roleLabel),
      React.createElement("div", { className: "xray-name" }, data.label),
      data.caption ? React.createElement("div", { className: "xray-path" }, data.caption) : null,
      React.createElement(Handle, { type: "source", position: Position.Bottom, id: "bottom" }),
      React.createElement(Handle, { type: "target", position: Position.Bottom, id: "bottom-in" }),
      React.createElement(Handle, { type: "source", position: Position.Right, id: "right" }),
    );
  }

  flowLibs = {
    React,
    createRoot,
    ReactFlow: flow.default,
    Background: flow.Background,
    Controls: flow.Controls,
    MarkerType: flow.MarkerType,
    nodeTypes: { symbol: SymbolNode },
  };
  return flowLibs;
}

function fallback(container, view) {
  const rows = view.nodes.map((node) => `${node.role || node.kind}: ${node.label}${node.file_path ? ` · ${node.file_path}` : ""}`);
  const edges = view.edges.map((edge) => `${edge.label}: ${edge.source} → ${edge.target}`);
  container.innerHTML = `<div class="flow-fallback">${[...rows, "", ...edges].map((line) => line.replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char]))).join("<br>")}</div>`;
}

async function mountGraph(container, view, onSelect, onFile) {
  const legend = document.querySelector("#graph-legend");
  if (legend) legend.hidden = view.view !== "neighborhood";
  try {
    const { React, createRoot, ReactFlow, Background, Controls, MarkerType, nodeTypes } = await libs();
    const nodes = layout(view);
    const visibleEdges = view.view === "neighborhood" ? view.edges.filter((edge) => edge.kind !== "CONTAINS") : view.edges;
    const yOf = Object.fromEntries(nodes.map((node) => [node.id, node.position.y]));
    const edges = visibleEdges.map((edge, index) => {
      const color = EDGE_COLOR[edge.kind] || "#8b97a9";
      const contains = edge.kind === "CONTAINS";
      const downward = (yOf[edge.source] ?? 0) <= (yOf[edge.target] ?? 0);
      return {
        id: `${edge.kind}-${edge.source}-${edge.target}-${index}`,
        source: edge.source,
        target: edge.target,
        sourceHandle: contains ? (downward ? "bottom" : "top-out") : "right",
        targetHandle: contains ? (downward ? "top" : "bottom-in") : "left",
        label: contains ? "" : edge.kind.toLowerCase(),
        type: contains ? "straight" : "smoothstep",
        style: { stroke: color, strokeWidth: edge.kind === "CALLS" ? 1.7 : 1.1, strokeDasharray: contains ? "4 4" : undefined },
        labelStyle: { fill: color, fontSize: 10, fontWeight: 650 },
        labelBgStyle: { fill: "#0c1016" },
        labelBgPadding: [4, 2],
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
      };
    });
    if (!reactRoot || reactRoot.container !== container) {
      container.replaceChildren();
      reactRoot = { container, root: createRoot(container) };
    }
    const signature = `${view.view}:${nodes.map((node) => node.id).join("|")}`;
    reactRoot.root.render(React.createElement(ReactFlow, {
      key: signature,
      nodes,
      edges,
      nodeTypes,
      fitView: true,
      fitViewOptions: { padding: 0.18 },
      minZoom: 0.25,
      nodesDraggable: true,
      proOptions: { hideAttribution: false },
      onNodeClick: (_event, node) => {
        if (node.id.startsWith("sym:")) onSelect(node.id);
        else if (node.id.startsWith("file:") && onFile) onFile(node.id.slice("file:".length));
      },
    }, React.createElement(Background, { color: "#243044", gap: 22, size: 1 }), React.createElement(Controls, null)));
  } catch (error) {
    fallback(container, view);
  }
}

window.XRayGraph = { mountGraph };
document.dispatchEvent(new Event("xray-graph"));
