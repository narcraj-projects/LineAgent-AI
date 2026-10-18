// Explicit synthetic API doubles; no hosted integration claims.
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import GraphAtlas from './GraphAtlas';
import { api } from './api';
import type { Catalog, ExplorerGraph, ServiceStatus } from './types';

vi.mock('./api', () => ({api:{explore:vi.fn()}}));
const status: ServiceStatus = {connected:true,graph_provider:'networkx',graph_mode:'fallback',vector_namespace:null,pdf_mode:true,demo_auth:true,synthetic:true};
const catalog = {alarms:[{code:'SYN-ALM-004',description:'Synthetic test',alarm_id:'SYN-ALARM-004',equipment_id:'SYN-EQ-002',failure_mode_id:'SYN-FM-004'},
  {code:'SYN-ALM-005',description:'Another synthetic test',alarm_id:'SYN-ALARM-005',equipment_id:'SYN-EQ-003',failure_mode_id:'SYN-FM-005'}]} as Catalog;
const graph: ExplorerGraph = {synthetic:true,status:{provider:'networkx',mode:'fallback',reason:'Unit double'},alarm_code:'SYN-ALM-004',equipment_id:'SYN-EQ-002',store_counts:{nodes:175,relationships:294},path_count:1,history_audited_against_sql:true,notice:'Test evidence, not a recommendation',
 nodes:[{id:'SYN-ALARM-004',kind:'alarm',title:'SYN-ALM-004',description:'Unit test alarm',outcomes:null,outcome:null,citation:null},
  {id:'SYN-FIX-004',kind:'fix',title:'Synthetic fix',description:'Simulator only',outcomes:{worked:2,did_not_work:1},outcome:null,citation:null},
  {id:'SYN-SEC-007',kind:'manual',title:'Synthetic manual',description:'Fictional provenance',outcomes:null,outcome:null,citation:{section_id:'SYN-SEC-007',filename:'linemedic-synthetic-manual-1.pdf',pdf_page:7,source_doc:'SYN-DOC-002',source_version:'1.0',logical_page:1}},
  {id:'SYN-WO-008',kind:'work_order',title:'SYN-WO-008',description:'Synthetic outcome',outcomes:null,outcome:'did_not_work',citation:null}],
 edges:[{id:'fixture-1',source:'SYN-ALARM-004',target:'SYN-FIX-004',relationship:'RESOLVED_BY'},{id:'fixture-2',source:'SYN-SEC-007',target:'SYN-FIX-004',relationship:'DESCRIBES'},{id:'fixture-3',source:'SYN-WO-008',target:'SYN-FIX-004',relationship:'APPLIED'}]};
beforeEach(()=>{vi.resetAllMocks();vi.mocked(api.explore).mockResolvedValue(graph);});
afterEach(cleanup);
function mount(connected=true) {return render(<GraphAtlas catalog={catalog} status={{...status,connected}} onConnect={vi.fn()} onInvestigate={vi.fn()} />);}
it('does not draw a sample graph or make calls before connection',()=>{
 mount(false);expect(screen.getByText('Your evidence network awaits.')).toBeTruthy();
 expect(screen.queryByLabelText('Interactive evidence graph')).toBeNull();expect(api.explore).not.toHaveBeenCalled();
});
it('inspects real returned IDs, current outcome denominators and exact PDF links',async()=>{
 mount();await screen.findByRole('button',{name:'Fix SYN-FIX-004'});
 fireEvent.click(screen.getByRole('button',{name:'Fix SYN-FIX-004'}));expect(screen.getByText(/67% recorded worked/)).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Manual section SYN-SEC-007'}));
 expect(screen.getByRole('link',{name:/Open verified page/}).getAttribute('href')).toContain('/pages/7.png');
 expect(screen.getByText('networkx / fallback')).toBeTruthy();
});
it('filters nodes, searches and supports keyboard selection plus zoom/layout controls',async()=>{
 mount();const node=await screen.findByRole('button',{name:'Fix SYN-FIX-004'});
 fireEvent.keyDown(node,{key:'Enter'});expect(node.getAttribute('aria-pressed')).toBe('true');
 fireEvent.click(screen.getByLabelText('Work orders'));expect(screen.queryByRole('button',{name:'Work order SYN-WO-008'})).toBeNull();
 fireEvent.change(screen.getByLabelText('Search graph nodes'),{target:{value:'SEC-007'}});expect(screen.getByText('1 matching nodes')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Zoom in graph'}));expect(screen.getByLabelText('Graph zoom').textContent).toBe('115%');
 fireEvent.click(screen.getByRole('button',{name:'Path view'}));expect(screen.getByLabelText('Graph zoom').textContent).toBe('100%');
});
it('clears old evidence when another alarm fails instead of displaying stale nodes',async()=>{
 mount();await screen.findByRole('button',{name:'Fix SYN-FIX-004'});
 vi.mocked(api.explore).mockRejectedValue(new Error('Graph SQL audit failed'));
 fireEvent.change(screen.getByLabelText('Graph alarm'),{target:{value:'SYN-ALM-005'}});
 await screen.findByText('Graph could not be verified.');expect(screen.queryByRole('button',{name:'Fix SYN-FIX-004'})).toBeNull();
 await waitFor(()=>expect(api.explore).toHaveBeenLastCalledWith('SYN-ALM-005'));
});
