const ROLE_COLOR = {
  focus: "#a9ef5c",
  caller: "#7eb8ff",
  callee: "#d2b4ff",
  class: "#f3f6fb",
  file: "#8b97a9",
  test: "#ffcf83",
  import: "#8b97a9",
  base: "#f3f6fb",
};

const ROLE_COLUMN = {
  caller: 0,
  test: 0,
  class: 430,
  focus: 430,
  file: 430,
  base: 430,
  callee: 860,
  import: 860,
};

let flowLibs = null;
let reactRoot = null;

function layout(nodes) {
  const seen = {};
  return nodes.map((node) => {
    const role = node.role || "file";
    const index = seen[role] || 0;
    seen[role] = index + 1;
    const yBase = role === "class" || role === "base" ? 0 : role === "file" || role === "test" || role === "import" ? 280 : 120;
    return {
      id: node.id,
      position: { x: ROLE_COLUMN[role] ?? 430, y: yBase + index * 92 },
      data: { label: node.file_path && node.kind !== "file" && node.kind !== "folder" ? `${node.label}\n${node.file_path}` : node.label },
      style: {
        width: 210,
        padding: 10,
        borderRadius: 8,
        border: `1px solid ${ROLE_COLOR[role] || "#283241"}`,
        background: "#141a24",
        color: "#f3f6fb",
        fontSize: 12,
        whiteSpace: "pre-wrap",
      },
    };
  });
}

async function libs() {
  if (flowLibs) return flowLibs;
  const React = (await import("https://esm.sh/react@18.3.1")).default;
  const { createRoot } = await import("https://esm.sh/react-dom@18.3.1/client");
  const flow = await import("https://esm.sh/reactflow@11.11.4?deps=react@18.3.1,react-dom@18.3.1");
  flowLibs = { React, createRoot, ReactFlow: flow.default, Background: flow.Background, Controls: flow.Controls };
  return flowLibs;
}

function fallback(container, view) {
  const rows = view.nodes.map((node) => `${node.role || node.kind}: ${node.label}${node.file_path ? ` · ${node.file_path}` : ""}`);
  const edges = view.edges.map((edge) => `${edge.label}: ${edge.source} → ${edge.target}`);
  container.innerHTML = `<div class="flow-fallback">${[...rows, "", ...edges].map((line) => line.replace(/[&<>]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[char]))).join("<br>")}</div>`;
}

async function mountGraph(container, view, onSelect) {
  try {
    const { React, createRoot, ReactFlow, Background, Controls } = await libs();
    const nodes = layout(view.nodes);
    const edges = view.edges.map((edge, index) => ({
      id: `${edge.kind}-${edge.source}-${edge.target}-${index}`,
      source: edge.source,
      target: edge.target,
      label: edge.label,
      style: { stroke: "#8b97a9" },
      labelStyle: { fill: "#8b97a9", fontSize: 10 },
    }));
    if (!reactRoot || reactRoot.container !== container) {
      container.replaceChildren();
      reactRoot = { container, root: createRoot(container) };
    }
    reactRoot.root.render(React.createElement(ReactFlow, {
      nodes,
      edges,
      fitView: true,
      nodesDraggable: true,
      onNodeClick: (_event, node) => {
        if (node.id.startsWith("sym:")) onSelect(node.id);
      },
    }, React.createElement(Background, null), React.createElement(Controls, null)));
  } catch (error) {
    fallback(container, view);
  }
}

window.XRayGraph = { mountGraph };
document.dispatchEvent(new Event("xray-graph"));
