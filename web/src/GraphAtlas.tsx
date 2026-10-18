import { useEffect, useMemo, useRef, useState, type PointerEvent } from 'react';
import { ArrowRight, BookOpen, Check, CircleAlert, Crosshair, ExternalLink, GitBranch, Layers3, LoaderCircle, Maximize2, Network, Plug, RefreshCw, Search, ShieldCheck, Sparkles, ZoomIn, ZoomOut } from 'lucide-react';
import { api } from './api';
import type { Catalog, ExplorerGraph, ExplorerNode, NodeKind, ServiceStatus } from './types';

const kinds: Record<NodeKind, { label: string; color: string; short: string }> = {
  equipment: { label: 'Equipment', color: '#89aaf9', short: 'EQ' },
  alarm: { label: 'Alarm', color: '#ffb17e', short: 'AL' },
  failure: { label: 'Failure mode', color: '#bba3ff', short: 'FM' },
  fix: { label: 'Fix', color: '#77e6b5', short: 'FX' },
  part: { label: 'Spare part', color: '#f1d47e', short: 'PT' },
  manual: { label: 'Manual section', color: '#7edbe8', short: 'PDF' },
  work_order: { label: 'Work order', color: '#d3abc7', short: 'WO' },
};
type Point = { x: number; y: number };
type Positioned = ExplorerNode & Point;

function positions(nodes: ExplorerNode[], layout: 'orbit' | 'path'): Positioned[] {
  const groups = new Map<NodeKind, ExplorerNode[]>();
  for (const node of nodes) groups.set(node.kind, [...(groups.get(node.kind) || []), node]);
  const order: NodeKind[] = ['equipment', 'alarm', 'failure', 'fix', 'part', 'manual', 'work_order'];
  return nodes.map(node => {
    const peers = groups.get(node.kind)!; const i = peers.findIndex(n => n.id === node.id);
    if (layout === 'path') {
      const column = order.indexOf(node.kind);
      return { ...node, x: 100 + column * 132, y: 138 + (i + 1) * 350 / (peers.length + 1) };
    }
    const centers: Record<NodeKind, Point> = { alarm: { x: 370, y: 295 }, failure: { x: 555, y: 200 },
      equipment: { x: 175, y: 230 }, fix: { x: 690, y: 320 }, part: { x: 875, y: 235 },
      manual: { x: 470, y: 485 }, work_order: { x: 180, y: 455 } };
    const center = centers[node.kind];
    const radius = peers.length === 1 ? 0 : Math.min(105, 46 + peers.length * 11);
    const angle = i / peers.length * Math.PI * 2 - Math.PI / 2;
    return { ...node, x: center.x + Math.cos(angle) * radius, y: center.y + Math.sin(angle) * radius * .6 };
  });
}

function shortId(id: string): string {
  if (id.startsWith('SYN-WO-FB-')) return `WO-FB-${id.slice(-6)}`;
  return id.replace('SYN-', '');
}

export default function GraphAtlas({ catalog, status, onInvestigate, onConnect, connecting = false }: {
  catalog: Catalog | null; status: ServiceStatus; onInvestigate: (alarm: string) => void;
  onConnect: () => void; connecting?: boolean;
}) {
  const [alarm, setAlarm] = useState('SYN-ALM-004');
  const [graph, setGraph] = useState<ExplorerGraph | null>(null);
  const [loading, setLoading] = useState(false); const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0); const [selected, setSelected] = useState<string | null>(null);
  const [layout, setLayout] = useState<'orbit' | 'path'>('orbit'); const [search, setSearch] = useState('');
  const [showManuals, setShowManuals] = useState(true); const [showHistory, setShowHistory] = useState(true);
  const [zoom, setZoom] = useState(1); const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const [fullscreen, setFullscreen] = useState(false);
  const drag = useRef<{ x: number; y: number; start: Point; id: number } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  useEffect(() => {
    let current = true; setGraph(null); setError(null); setSelected(null); setPan({x:0,y:0}); setZoom(1);
    if (!status.connected) { setLoading(false); return; }
    setLoading(true);
    api.explore(alarm).then(value => { if (current) { setGraph(value); setSelected(value.nodes.find(n => n.kind === 'alarm')?.id || null); } })
      .catch(reason => { if (current) setError(reason instanceof Error ? reason.message : 'Graph retrieval failed.'); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [alarm, status.connected, revision]);
  const nodes = useMemo(() => positions((graph?.nodes || []).filter(n => (showManuals || n.kind !== 'manual') && (showHistory || n.kind !== 'work_order')), layout), [graph, showManuals, showHistory, layout]);
  const nodeMap = new Map(nodes.map(n => [n.id, n]));
  const edges = (graph?.edges || []).filter(e => nodeMap.has(e.source) && nodeMap.has(e.target));
  const picked = graph?.nodes.find(n => n.id === selected) || null;
  const neighbors = new Set(edges.filter(e => e.source === selected || e.target === selected).flatMap(e => [e.source, e.target]));
  const matches = nodes.filter(n => !search || `${n.id} ${n.title} ${n.description}`.toLowerCase().includes(search.toLowerCase()));
  const matchIds = new Set(matches.map(n => n.id));
  const fixes = graph?.nodes.filter(n => n.kind === 'fix') || [];
  const workOrders = graph?.nodes.filter(n => n.kind === 'work_order') || [];
  function reset() { setZoom(1); setPan({x:0,y:0}); }
  function startDrag(event: PointerEvent<SVGSVGElement>) {
    if ((event.target as Element).closest('[data-node]')) return;
    drag.current = { x: event.clientX, y: event.clientY, start: pan, id: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function moveDrag(event: PointerEvent<SVGSVGElement>) {
    if (!drag.current || !svg.current) return;
    const scale = 1000 / svg.current.getBoundingClientRect().width;
    setPan({ x: drag.current.start.x + (event.clientX - drag.current.x) * scale,
      y: drag.current.start.y + (event.clientY - drag.current.y) * scale });
  }
  function selectNode(node: ExplorerNode) { setSelected(node.id); }
  return <section className={`atlas ${fullscreen ? 'atlas-expanded' : ''}`}>
    <div className="atlas-intro"><div><span className="atlas-eyebrow"><Network size={13} /> EXPLORE THE CONNECTIONS</span><h1>Every alarm has a story<span>.</span></h1><p>Follow the evidence from machine to manual. Select a node to see what connects.</p></div><div className="atlas-provider"><span className={`status-dot ${graph ? 'on' : ''}`} /><span role="status" aria-label="Graph provider">{graph ? `${graph.status.provider} / ${graph.status.mode}` : 'Connect to explore'}</span><small>{graph ? 'Retrieved from the selected graph adapter' : 'No sample network is presented as live data'}</small></div></div>
    <div className="atlas-toolbar"><label className="atlas-alarm"><CircleAlert size={16} /><span>Alarm</span><select aria-label="Graph alarm" value={alarm} onChange={e => setAlarm(e.target.value)}>{(catalog?.alarms || []).map(a => <option key={a.code} value={a.code}>{a.code} · {a.description.replace('Fictional alarm: ', '')}</option>)}</select></label><label className="atlas-search"><Search size={16} /><input aria-label="Search graph nodes" value={search} onChange={e => setSearch(e.target.value)} placeholder="Find an ID, part or source…" /></label><button className="button secondary" disabled={!status.connected || loading} onClick={() => setRevision(n => n + 1)}><RefreshCw size={14} className={loading ? 'spin' : ''} />Refresh graph</button></div>
    <div className="atlas-layout"><div className="atlas-stage">
      <div className="atlas-stage-top"><div className="atlas-view-switch" aria-label="Graph layout"><button className={layout === 'orbit' ? 'selected' : ''} onClick={() => {setLayout('orbit');reset();}}><Sparkles size={13} />Constellation</button><button className={layout === 'path' ? 'selected' : ''} onClick={() => {setLayout('path');reset();}}><GitBranch size={13} />Path view</button></div><button className="atlas-icon" aria-label={fullscreen ? 'Exit expanded graph' : 'Expand graph'} onClick={() => setFullscreen(v => !v)}><Maximize2 size={16} /></button></div>
      {!status.connected ? <div className="atlas-state"><div className="atlas-state-icon"><Network size={40} /></div><h2>Your evidence network awaits.</h2><p>Connect services to retrieve actual graph paths and audit their history against SQL.</p><button className="button primary" onClick={onConnect} disabled={connecting}><Plug size={15} />{connecting ? 'Connecting…' : 'Connect live services'}</button></div>
       : loading ? <div className="atlas-state" role="status"><LoaderCircle size={35} className="spin" /><h2>Tracing the evidence…</h2><p>Retrieving paths and checking recorded outcomes.</p></div>
       : error ? <div className="atlas-state atlas-error" role="alert"><CircleAlert size={34} /><h2>Graph could not be verified.</h2><p>{error}</p><button className="button secondary" onClick={() => setRevision(n => n + 1)}>Try again</button></div>
       : !nodes.length ? <div className="atlas-state"><Network size={36} /><h2>No paths returned.</h2><p>Choose another alarm or restore the evidence filters.</p></div>
       : <><svg ref={svg} className="atlas-canvas" viewBox="0 0 1000 660" aria-label="Interactive evidence graph" onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={() => {drag.current = null;}} onPointerCancel={() => {drag.current = null;}}>
         <defs><pattern id="atlas-grid" width="26" height="26" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r=".7" fill="#476176" opacity=".46" /></pattern><radialGradient id="atlas-glow"><stop stopColor="#164958" stopOpacity=".65" /><stop offset="1" stopColor="#0c1928" stopOpacity="0" /></radialGradient><marker id="atlas-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#7995a6" /></marker></defs>
         <rect width="1000" height="660" fill="url(#atlas-grid)" /><ellipse cx="530" cy="330" rx="465" ry="300" fill="url(#atlas-glow)" />
         <g transform={`translate(${pan.x + 500 * (1 - zoom)} ${pan.y + 330 * (1 - zoom)}) scale(${zoom})`}>
           {layout === 'orbit' && <g className="atlas-orbits"><ellipse cx="500" cy="330" rx="330" ry="170" /><ellipse cx="500" cy="330" rx="435" ry="260" /></g>}
           {edges.map(e => { const from = nodeMap.get(e.source)!; const to = nodeMap.get(e.target)!; const active = e.source === selected || e.target === selected;
             const dx=to.x-from.x, dy=to.y-from.y, distance=Math.hypot(dx,dy)||1;
             const sx=from.x+dx/distance*28, sy=from.y+dy/distance*28, tx=to.x-dx/distance*32, ty=to.y-dy/distance*32;
             return <g key={e.id} className={`atlas-link ${active ? 'is-lit' : ''}`}><line x1={sx} y1={sy} x2={tx} y2={ty} markerEnd="url(#atlas-arrow)" /><title>{`${e.source} ${e.relationship} ${e.target}`}</title>{active && <text x={(sx+tx)/2} y={(sy+ty)/2-8} textAnchor="middle">{e.relationship}</text>}</g>;
           })}
           {nodes.map(n => { const info=kinds[n.kind]; const active=n.id===selected; const dim=search ? !matchIds.has(n.id) : !!selected && !active && !neighbors.has(n.id);
             return <g key={n.id} data-node={n.id} role="button" tabIndex={0} aria-label={`${info.label} ${n.id}`} aria-pressed={active} transform={`translate(${n.x} ${n.y})`} className={`atlas-node ${active ? 'is-selected' : ''} ${dim ? 'is-dim' : ''}`} onClick={() => selectNode(n)} onKeyDown={e => {if(e.key==='Enter'||e.key===' '){e.preventDefault();selectNode(n);}}}>
               <title>{`${n.id}: ${n.title}`}</title><circle r={active ? 39 : 32} className="atlas-node-aura" stroke={info.color} /><circle r="25" fill="#132a38" stroke={info.color} strokeWidth={active ? 2.5 : 1.5} /><text className="atlas-node-glyph" fill={info.color} textAnchor="middle" y="4">{info.short}</text><text className="atlas-node-id" textAnchor="middle" y="48">{shortId(n.id)}</text>{active && <circle r="3" cx="30" cy="-28" fill={info.color} />}
             </g>;
           })}
         </g>
       </svg><div className="atlas-canvas-caption"><span><span className="atlas-pulse" />{nodes.length} visible entities · {edges.length} relationships</span><small>{search ? `${matches.length} matching nodes` : 'Drag to pan · use + / − to zoom · select a node'}</small></div><div className="atlas-zoom"><button aria-label="Zoom out graph" onClick={() => setZoom(v => Math.max(.55,v-.15))}><ZoomOut size={16} /></button><output aria-label="Graph zoom">{Math.round(zoom*100)}%</output><button aria-label="Zoom in graph" onClick={() => setZoom(v => Math.min(2.2,v+.15))}><ZoomIn size={16} /></button><button aria-label="Reset graph view" onClick={reset}><Crosshair size={16} /></button></div></>}
    </div><aside className="atlas-inspector"><div className="atlas-inspector-heading"><span>ENTITY INSPECTOR</span><span className="atlas-inspect-icon"><Layers3 size={17} /></span></div>{picked ? <><div className="atlas-kind" style={{color:kinds[picked.kind].color}}><span style={{background:kinds[picked.kind].color}} />{kinds[picked.kind].label}</div><h2>{picked.title}</h2><code className="atlas-entity-id">{picked.id}</code><p>{picked.description}</p>
      {picked.outcomes && <div className="atlas-history"><span>RECORDED OUTCOMES</span><strong>{picked.outcomes.worked}<small> / {picked.outcomes.worked+picked.outcomes.did_not_work} worked</small></strong><div className="atlas-outcome-bar"><i style={{width:`${picked.outcomes.worked+picked.outcomes.did_not_work ? picked.outcomes.worked/(picked.outcomes.worked+picked.outcomes.did_not_work)*100 : 0}%`}} /></div><p>{picked.outcomes.worked+picked.outcomes.did_not_work ? `${Math.round(picked.outcomes.worked/(picked.outcomes.worked+picked.outcomes.did_not_work)*100)}% recorded worked` : 'Rate unknown: no recorded attempts'} · SQL-audited synthetic history.</p></div>}
      {picked.outcome && <div className={`atlas-outcome ${picked.outcome}`}><Check size={14} />Recorded: {picked.outcome.replaceAll('_',' ')}</div>}
      {picked.citation && <div className="atlas-citation"><BookOpen size={19} /><div><strong>{picked.citation.source_doc}</strong><span>Logical page {picked.citation.logical_page} · physical PDF page {picked.citation.pdf_page}</span></div><a href={`/api/manuals/${picked.citation.filename}/pages/${picked.citation.pdf_page}.png`} target="_blank" rel="noreferrer">Open verified page <ExternalLink size={12} /></a></div>}
      <div className="atlas-neighbors"><span>CONNECTED RELATIONSHIPS</span>{(graph?.edges || []).filter(e=>e.source===picked.id||e.target===picked.id).map(e=>{const other=graph!.nodes.find(n=>n.id===(e.source===picked.id?e.target:e.source))!;return <button key={e.id} onClick={()=>{if(other.kind==='manual')setShowManuals(true);if(other.kind==='work_order')setShowHistory(true);setSelected(other.id);}}><span>{e.relationship}<small>{e.source===picked.id ? 'outgoing' : 'incoming'}</small></span><strong>{shortId(other.id)}</strong><ArrowRight size={13} /></button>;})}</div>
      </> : <div className="atlas-inspector-empty"><Crosshair size={32} /><h3>Select a connection.</h3><p>Entity details, recorded outcomes and PDF provenance appear here.</p></div>}
      <div className="atlas-audit"><ShieldCheck size={16} /><p>{graph ? 'Returned histories were checked against SQL. Relationships do not imply a recommendation.' : 'Verified graph data will appear after connection.'}</p></div></aside></div>
    <div className="atlas-bottom"><div className="atlas-legend">{Object.entries(kinds).map(([key,kind])=><span key={key}><i style={{background:kind.color}} />{kind.label}</span>)}</div><div className="atlas-filters"><label><input type="checkbox" checked={showManuals} onChange={e=>{setShowManuals(e.target.checked);if(!e.target.checked&&picked?.kind==='manual')setSelected(null);}} />Manuals</label><label><input type="checkbox" checked={showHistory} onChange={e=>{setShowHistory(e.target.checked);if(!e.target.checked&&picked?.kind==='work_order')setSelected(null);}} />Work orders</label></div></div>
    <div className="atlas-summary"><div><span>EXPLORED SUBGRAPH</span><strong>{graph?.path_count ?? '—'}<small>retrieved fix paths</small></strong></div><div><span>EVIDENCE IN CONTEXT</span><strong>{workOrders.length}<small>distinct work orders · {fixes.length} linked fixes</small></strong></div><div><span>FULL GRAPH STORE</span><strong>{graph?.store_counts.nodes ?? '—'}<small>nodes · {graph?.store_counts.relationships ?? '—'} relationships</small></strong></div><button className="button primary" onClick={()=>onInvestigate(alarm)}><Search size={15} />Investigate this alarm <ArrowRight size={15} /></button></div>
  </section>;
}
