"use client";

import "@xyflow/react/dist/style.css";
import * as React from "react";
import {
  ReactFlow, Background, Controls, MiniMap, Handle, Position, addEdge,
  useNodesState, useEdgesState,
  type Connection, type Edge, type Node, type NodeProps, type NodeTypes
} from "@xyflow/react";
import { create } from "zustand";
import {
  Bot, BrainCircuit, Braces, CheckCircle2, Database, GitBranch, Globe2, Hourglass,
  Layers3, MemoryStick, Play, Plus, RefreshCw, Save, ShieldCheck, Sparkles, Trash2, Workflow
} from "lucide-react";
import { useApiResource, useFoundry } from "@/components/auth-gate";
import { useRealtimeRefresh } from "@/components/realtime";
import {
  Badge, Button, Card, CardContent, CardHeader, CardTitle, EmptyState, Input, JsonBlock,
  Label, Select, StatusPill, Tabs, TabsContent, TabsList, TabsTrigger, Textarea, InlineNotice
} from "@/components/ui";
import { NODE_LABELS, NODE_TYPES, type FoundryNodeType, type WorkflowGraph } from "@/lib/types";
import { cn, safeJson } from "@/lib/utils";

type BuilderNodeData = Record<string, unknown> & {
  label: string;
  nodeType: FoundryNodeType;
  config: Record<string, unknown>;
  status?: string;
};

type BuilderNode = Node<BuilderNodeData, "foundryNode">;

type BuilderState = {
  workflowId: string | null;
  selectedNodeId: string | null;
  activeRunId: string | null;
  status: string | null;
  setWorkflowId: (id: string | null) => void;
  setSelectedNodeId: (id: string | null) => void;
  setActiveRunId: (id: string | null) => void;
  setStatus: (status: string | null) => void;
};

const useBuilderStore = create<BuilderState>((set) => ({
  workflowId: null, selectedNodeId: null, activeRunId: null, status: null,
  setWorkflowId: (workflowId) => set({ workflowId }),
  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),
  setActiveRunId: (activeRunId) => set({ activeRunId }),
  setStatus: (status) => set({ status })
}));

const NODE_ICON: Record<FoundryNodeType, React.ComponentType<{ className?: string }>> = {
  agent: Bot, llm: BrainCircuit, tool: Braces, mcp_server: Database, http_request: Globe2,
  condition: GitBranch, router: Workflow, loop: RefreshCw, memory: MemoryStick,
  knowledge_retrieval: Layers3, human_approval: ShieldCheck, task: CheckCircle2, output: Sparkles
};

const NODE_TONE: Record<FoundryNodeType, string> = {
  agent: "text-signal-cyan border-signal-cyan/30", llm: "text-signal-blue border-signal-blue/30",
  tool: "text-signal-amber border-signal-amber/30", mcp_server: "text-signal-violet border-signal-violet/30",
  http_request: "text-signal-blue border-signal-blue/30", condition: "text-signal-amber border-signal-amber/30",
  router: "text-signal-violet border-signal-violet/30", loop: "text-signal-rose border-signal-rose/30",
  memory: "text-signal-green border-signal-green/30", knowledge_retrieval: "text-signal-green border-signal-green/30",
  human_approval: "text-signal-amber border-signal-amber/30", task: "text-signal-cyan border-signal-cyan/30",
  output: "text-signal-green border-signal-green/30"
};

function FoundryFlowNode({ data, selected }: NodeProps) {
  const node = data as unknown as BuilderNodeData;
  const Icon = NODE_ICON[node.nodeType] ?? Bot;
  return (
    <div className={cn(
      "w-44 rounded-lg border bg-graphite-900/95 px-2.5 py-2 shadow-panel transition-all",
      NODE_TONE[node.nodeType],
      selected && "ring-1 ring-signal-cyan/60 shadow-glow",
      node.status === "running" && "animate-soft-pulse",
      node.status === "completed" && "border-signal-green/50",
      node.status === "failed" && "border-signal-rose/50",
      node.status === "skipped" && "opacity-50"
    )}>
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center gap-2">
        <div className="flex h-6 w-6 items-center justify-center rounded border border-current/20 bg-current/10"><Icon className="h-3.5 w-3.5" /></div>
        <div className="min-w-0"><div className="truncate text-2xs font-semibold text-ink">{node.label}</div><div className="font-mono text-[9px] uppercase tracking-wider text-ink-faint">{NODE_LABELS[node.nodeType]}</div></div>
      </div>
      {node.status ? <div className="mt-1.5"><StatusPill status={node.status} /></div> : null}
      <Handle type="source" position={Position.Right} />
      {node.nodeType === "condition" || node.nodeType === "router" ? <Handle id="true" type="source" position={Position.Bottom} className="!left-8 !bg-signal-green" /> : null}
      {node.nodeType === "condition" || node.nodeType === "router" ? <Handle id="false" type="source" position={Position.Bottom} className="!left-24 !bg-signal-rose" /> : null}
    </div>
  );
}

const nodeTypes: NodeTypes = { foundryNode: FoundryFlowNode };

type WorkflowRow = { id: string; name: string; description: string; graph: WorkflowGraph; is_template: boolean };
type AgentRow = { id: string; name: string };
type TaskRow = { id: string; key: string; node_id: string; status: string; attempts: number; output?: unknown; last_error?: string | null };

const DEFAULT_CONFIG: Record<FoundryNodeType, Record<string, unknown>> = {
  agent: { assigneeAgentId: "", prompt: "Execute the assigned objective.", timeoutMs: 120000, retry: { maxAttempts: 2 } },
  llm: { prompt: "Return a concise structured result.", temperature: 0.2, maxTokens: 4096 },
  tool: { toolName: "", arguments: {}, resourceLock: "" },
  mcp_server: { toolName: "", arguments: {}, resourceLock: "" },
  http_request: { toolId: "", method: "GET", arguments: {} },
  condition: { path: "dependencies.0.output.ok", equals: true },
  router: { routes: [{ handle: "default", when: { path: "ok", equals: true } }], defaultHandle: "default" },
  loop: { iterations: 3, itemsPath: "", transform: { mapping: { index: "index" } } },
  memory: { operation: "search", scope: "agent", query: "objective", limit: 5 },
  knowledge_retrieval: { query: "objective", limit: 5 },
  human_approval: { reason: "Review the planned changes before execution.", input: {} },
  task: { title: "Durable task", resourceLock: "" },
  output: { format: "json" }
};

function toFlowNodes(graph: WorkflowGraph | undefined): BuilderNode[] {
  return (graph?.nodes ?? []).map((node) => ({
    id: node.id, type: "foundryNode", position: node.position,
    data: { label: node.data.label, nodeType: node.type, config: node.data.config }
  }));
}

function toFlowEdges(graph: WorkflowGraph | undefined): Edge[] {
  return (graph?.edges ?? []).map((edge) => ({
    id: edge.id, source: edge.source, target: edge.target,
    sourceHandle: edge.sourceHandle ?? undefined, targetHandle: edge.targetHandle ?? undefined,
    label: edge.label, animated: Boolean(edge.sourceHandle), style: edge.sourceHandle ? { stroke: "#73d6cf" } : undefined
  }));
}

export function FlowBuilder() {
  const { apiFetch, config } = useFoundry();
  const store = useBuilderStore();
  const workflows = useApiResource<WorkflowRow[]>("/api/foundry/workflows");
  const agents = useApiResource<AgentRow[]>("/api/foundry/agents");
  const tasks = useApiResource<TaskRow[]>(store.activeRunId ? `/api/foundry/runs/${store.activeRunId}/tasks` : null);
  const [nodes, setNodes, onNodesChange] = useNodesState<BuilderNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [configText, setConfigText] = useState("{}");
  const [newWorkflowName, setNewWorkflowName] = useState("New orchestration");
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [validation, setValidation] = useState<string[]>([]);

  const workflow = React.useMemo(() => workflows.data?.find((item) => item.id === store.workflowId) ?? workflows.data?.[0] ?? null, [workflows.data, store.workflowId]);

  React.useEffect(() => {
    if (!workflow) return;
    useBuilderStore.getState().setWorkflowId(workflow.id);
    setNodes(toFlowNodes(workflow.graph));
    setEdges(toFlowEdges(workflow.graph));
  }, [workflow?.id, setNodes, setEdges]);

  React.useEffect(() => {
    const selected = nodes.find((node) => node.id === store.selectedNodeId);
    setConfigText(selected ? safeJson(selected.data.config) : "{}");
  }, [store.selectedNodeId, nodes]);

  const statusByNode = React.useMemo(() => {
    const map = new Map<string, TaskRow>();
    for (const task of tasks.data ?? []) map.set(task.node_id || task.key, task);
    return map;
  }, [tasks.data]);

  React.useEffect(() => {
    setNodes((current) => current.map((node) => ({ ...node, data: { ...node.data, status: statusByNode.get(node.id)?.status ?? undefined } })));
  }, [statusByNode, setNodes]);

  const refresh = React.useCallback(() => { workflows.refresh(); if (store.activeRunId) tasks.refresh(); }, [workflows.refresh, tasks.refresh, store.activeRunId]);
  const realtime = useRealtimeRefresh(refresh, ["tasks", "workflow_runs", "events"]);

  const graph = React.useMemo<WorkflowGraph>(() => ({
    nodes: nodes.map((node) => ({ id: node.id, type: node.data.nodeType, position: node.position, data: { label: node.data.label, config: node.data.config, status: node.data.status } })),
    edges: edges.map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, sourceHandle: edge.sourceHandle ?? null, targetHandle: edge.targetHandle ?? null, label: edge.label ? String(edge.label) : undefined }))
  }), [nodes, edges]);

  const addNode = (type: FoundryNodeType) => {
    const id = `${type}-${Math.random().toString(36).slice(2, 7)}`;
    setNodes((current) => [...current, { id, type: "foundryNode", position: { x: 120 + current.length * 40, y: 80 + (current.length % 4) * 100 }, data: { label: NODE_LABELS[type], nodeType: type, config: DEFAULT_CONFIG[type] } }]);
    store.setSelectedNodeId(id);
  };

  const applyConfig = () => {
    try {
      const parsed = JSON.parse(configText) as Record<string, unknown>;
      setNodes((current) => current.map((node) => node.id === store.selectedNodeId ? { ...node, data: { ...node.data, config: parsed } } : node));
      store.setStatus("Node configuration applied locally. Save the workflow to persist it.");
    } catch (error) { store.setStatus(`Invalid JSON: ${error instanceof Error ? error.message : String(error)}`); }
  };

  const createWorkflow = async () => {
    if (!config.databaseConfigured || !config.authConfigured) { store.setStatus("Database and Auth must be configured before saving workflows."); return; }
    try {
      const created = await apiFetch<WorkflowRow>("/api/foundry/workflows", { method: "POST", body: JSON.stringify({ name: newWorkflowName, description: "Created in the visual builder.", graph }) });
      store.setWorkflowId(created.id); workflows.refresh(); store.setStatus("Workflow created.");
    } catch (error) { store.setStatus(error instanceof Error ? error.message : String(error)); }
  };

  const saveWorkflow = async () => {
    if (!workflow) return createWorkflow();
    setSaving(true); store.setStatus(null); setValidation([]);
    try {
      await apiFetch(`/api/foundry/workflows/${workflow.id}`, { method: "PATCH", body: JSON.stringify({ graph }) });
      workflows.refresh(); store.setStatus("Workflow graph saved and validated.");
    } catch (error) { store.setStatus(error instanceof Error ? error.message : String(error)); }
    finally { setSaving(false); }
  };

  const runWorkflow = async () => {
    if (!workflow) { store.setStatus("Create or select a workflow first."); return; }
    setRunning(true);
    try {
      const result = await apiFetch<{ runId: string; queued: boolean }>(`/api/foundry/workflows/${workflow.id}/runs`, { method: "POST", body: JSON.stringify({ mode: "graph", input: { objective: "Run from visual builder" } }) });
      store.setActiveRunId(result.runId);
      store.setStatus(`Run ${result.runId.slice(0, 8)} queued. A persistent worker must be online for execution.`);
      setTimeout(() => tasks.refresh(), 800);
    } catch (error) { store.setStatus(error instanceof Error ? error.message : String(error)); }
    finally { setRunning(false); }
  };

  const selectedTask = store.selectedNodeId ? statusByNode.get(store.selectedNodeId) : undefined;

  return (
    <div className="space-y-4">
      {store.status ? <InlineNotice tone={store.status.includes("Invalid") || store.status.includes("must") ? "amber" : "cyan"}>{store.status}</InlineNotice> : null}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <Card className="overflow-hidden">
          <CardHeader className="flex-wrap gap-2">
            <div className="min-w-0">
              <CardTitle>Visual execution graph</CardTitle>
              <div className="mt-1 flex items-center gap-2 text-2xs text-ink-faint">
                <span className="font-mono">{workflow?.name ?? "Unsaved workflow"}</span>
                <Badge tone="neutral">{nodes.length} nodes</Badge><Badge tone="neutral">{edges.length} edges</Badge>
                <Badge tone={realtime === "subscribed" ? "green" : "amber"}>realtime {realtime}</Badge>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Select className="w-44" value={workflow?.id ?? ""} onChange={(e) => store.setWorkflowId(e.target.value)}>
                {(workflows.data ?? []).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                {!workflows.data?.length ? <option value="">No saved workflows</option> : null}
              </Select>
              <Button variant="secondary" onClick={() => workflows.refresh()}><RefreshCw className="h-3.5 w-3.5" /></Button>
              <Button variant="secondary" onClick={saveWorkflow} disabled={saving}><Save className="h-3.5 w-3.5" />{saving ? "Saving" : "Save"}</Button>
              <Button onClick={runWorkflow} disabled={running}><Play className="h-3.5 w-3.5" />{running ? "Queuing" : "Run"}</Button>
            </div>
          </CardHeader>
          <div className="grid grid-cols-2 gap-1.5 border-b border-line bg-graphite-950/50 p-2 sm:grid-cols-4 lg:grid-cols-7">
            {NODE_TYPES.map((type) => {
              const Icon = NODE_ICON[type];
              return <button key={type} onClick={() => addNode(type)} className="flex h-7 items-center justify-center gap-1.5 rounded border border-line bg-graphite-900 text-2xs text-ink-muted hover:border-signal-cyan/40 hover:text-ink"><Icon className="h-3 w-3" />{NODE_LABELS[type]}</button>;
            })}
          </div>
          <div className="h-[560px] bg-graphite-950/80">
            <ReactFlow
              nodes={nodes} edges={edges} nodeTypes={nodeTypes}
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              onConnect={(connection: Connection) => setEdges((current) => addEdge({ ...connection, animated: Boolean(connection.sourceHandle), style: connection.sourceHandle ? { stroke: "#73d6cf" } : undefined }, current))}
              onNodeClick={(_, node) => store.setSelectedNodeId(node.id)}
              fitView proOptions={{ hideAttribution: true }} minZoom={0.25} maxZoom={1.8}
              defaultEdgeOptions={{ type: "smoothstep" }}
            >
              <Background color="#2a323d" gap={22} size={1} />
              <Controls /><MiniMap pannable zoomable nodeColor="#202730" maskColor="rgba(9,11,14,0.72)" />
            </ReactFlow>
          </div>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Node inspector</CardTitle><Badge tone="neutral">{nodes.find((node) => node.id === store.selectedNodeId)?.data.nodeType ?? "none"}</Badge></CardHeader>
            <CardContent className="space-y-3">
              {!store.selectedNodeId ? <EmptyState title="Select a node" description="Click any node to edit its label, config, retry, timeout and resource lock." /> : (
                <>
                  <div><Label>Label</Label><Input value={nodes.find((node) => node.id === store.selectedNodeId)?.data.label ?? ""} onChange={(e) => setNodes((current) => current.map((node) => node.id === store.selectedNodeId ? { ...node, data: { ...node.data, label: e.target.value } } : node))} /></div>
                  <div><Label>Agent assignment (Agent nodes)</Label><Select value={String(nodes.find((node) => node.id === store.selectedNodeId)?.data.config.assigneeAgentId ?? "")} onChange={(e) => {
                    const id = e.target.value; setNodes((current) => current.map((node) => node.id === store.selectedNodeId ? { ...node, data: { ...node.data, config: { ...node.data.config, assigneeAgentId: id } } } : node));
                  }}><option value="">No agent</option>{(agents.data ?? []).map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</Select></div>
                  <div><Label>Node config (JSON)</Label><Textarea rows={12} value={configText} onChange={(e) => setConfigText(e.target.value)} /></div>
                  <div className="flex gap-2"><Button className="flex-1" onClick={applyConfig}>Apply config</Button><Button variant="danger" size="icon" onClick={() => { setNodes((current) => current.filter((node) => node.id !== store.selectedNodeId)); setEdges((current) => current.filter((edge) => edge.source !== store.selectedNodeId && edge.target !== store.selectedNodeId)); store.setSelectedNodeId(null); }}><Trash2 className="h-3.5 w-3.5" /></Button></div>
                  <p className="text-2xs leading-relaxed text-ink-faint">Use <span className="font-mono text-ink-muted">resourceLock</span> to serialize writes, <span className="font-mono text-ink-muted">retry.maxAttempts</span> for recoverable failures, and <span className="font-mono text-ink-muted">timeoutMs</span> for execution limits.</p>
                </>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Create workflow</CardTitle></CardHeader>
            <CardContent className="space-y-2"><Label>Name</Label><Input value={newWorkflowName} onChange={(e) => setNewWorkflowName(e.target.value)} /><Button variant="secondary" className="w-full" onClick={createWorkflow}><Plus className="h-3.5 w-3.5" />Create from current graph</Button></CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>Execution state</CardTitle>{store.activeRunId ? <Badge tone="cyan">{store.activeRunId.slice(0, 8)}</Badge> : <Badge tone="neutral">idle</Badge>}</CardHeader>
            <CardContent className="space-y-2">
              <Tabs defaultValue="results">
                <TabsList><TabsTrigger value="results">Results</TabsTrigger><TabsTrigger value="tasks">Tasks</TabsTrigger></TabsList>
                <TabsContent value="results" className="mt-3">{selectedTask ? <JsonBlock value={selectedTask.output ?? { status: selectedTask.status, error: selectedTask.last_error }} /> : <p className="text-xs text-ink-faint">Run the graph to inspect intermediate outputs and active-node status.</p>}</TabsContent>
                <TabsContent value="tasks" className="mt-3 space-y-1.5">{(tasks.data ?? []).map((task) => <button key={task.id} onClick={() => store.setSelectedNodeId(task.node_id || task.key)} className="flex w-full items-center justify-between rounded border border-line bg-graphite-950/60 px-2 py-1.5 text-left text-2xs hover:border-signal-cyan/30"><span className="truncate font-mono">{task.key}</span><StatusPill status={task.status} /></button>)}{!tasks.data?.length ? <p className="text-xs text-ink-faint">No durable tasks yet.</p> : null}</TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function useState<T>(initial: T): [T, (value: T | ((current: T) => T)) => void] {
  return React.useState(initial) as [T, (value: T | ((current: T) => T)) => void];
}
