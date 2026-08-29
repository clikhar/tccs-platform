import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type State = 'ONLINE' | 'OFFLINE' | 'CALLING' | 'MUTED';
type ApiStation = { id: number; station_number: string; name: string; location: string | null; section_id: number; sip_extension: string; station_type: string; enabled: boolean; priority: number };
type Station = { id: string; name: string; state: State; sipExtension: string; location: string };
type Participant = { id: string; name: string; state: 'LISTENING' | 'MUTED' | 'TALKING' };

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

function mapStation(s: ApiStation): Station {
  return { id: s.station_number, name: s.name, state: 'ONLINE', sipExtension: s.sip_extension, location: s.location || '' };
}

function App() {
  const [stations, setStations] = useState<Station[]>([]);
  const [loading, setLoading] = useState(true);
  const [apiError, setApiError] = useState<string | null>(null);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [modal, setModal] = useState<string | null>(null);
  const [message, setMessage] = useState('Connecting to TCCS Controller API...');
  const online = useMemo(() => stations.filter(s => s.state !== 'OFFLINE').length, [stations]);

  const loadStations = async () => {
    setLoading(true); setApiError(null);
    try {
      const response = await fetch(`${API_BASE}/api/v1/stations`);
      if (!response.ok) throw new Error(`API returned HTTP ${response.status}`);
      const data: ApiStation[] = await response.json();
      setStations(data.map(mapStation));
      setMessage(`Connected • ${data.length} stations loaded`);
    } catch (error) {
      const text = error instanceof Error ? error.message : 'Unable to reach API';
      setApiError(text); setMessage('Controller API unavailable');
    } finally { setLoading(false); }
  };

  useEffect(() => { loadStations(); }, []);

  // In TCCS operation, clicking a station initiates its call and places it
  // directly into the existing active conference. No confirmation dialog.
  const callStation = (station: Station) => {
    if (station.state === 'OFFLINE') {
      setMessage(`Station ${station.id} is offline`);
      return;
    }
    setParticipants(current => current.some(p => p.id === station.id)
      ? current
      : [...current, { id: station.id, name: station.name, state: 'LISTENING' }]);
    setStations(current => current.map(s => s.id === station.id ? { ...s, state: 'CALLING' } : s));
    setMessage(`Calling station ${station.id} • added to active conference`);
  };

  const generalCall = () => {
    setParticipants(stations.filter(s => s.state !== 'OFFLINE').map(s => ({ id: s.id, name: s.name, state: 'LISTENING' })));
    setMessage('General call prepared for all available stations');
  };
  const toggleMute = (id: string) => setParticipants(current => current.map(p => p.id === id ? { ...p, state: p.state === 'MUTED' ? 'LISTENING' : 'MUTED' } : p));
  const remove = (id: string) => {
    setParticipants(current => current.filter(p => p.id !== id));
    setStations(current => current.map(s => s.id === id ? { ...s, state: 'ONLINE' } : s));
  };

  return <div className="app">
    <header className="topbar"><div><div className="brand">TCCS</div><div className="subtitle">Train Control Communication System</div></div><div className="top-status"><span>SECTION <b>01</b></span><span>CONTROLLER <b>C01</b></span><span className={apiError ? 'error' : 'ok'}>● {apiError ? 'API OFFLINE' : loading ? 'CONNECTING' : 'SYSTEM NORMAL'}</span></div></header>
    <main>
      <div className="heading"><div><h1>Controller Console</h1><p>Stage 2 • Controller UI • Live API</p></div><div className="toolbar"><button onClick={() => setModal('directory')}>Directory</button><button onClick={() => setModal('history')}>Call History</button><button onClick={() => setModal('settings')}>Settings</button></div></div>
      {apiError && <div className="api-error"><b>Controller API unavailable.</b> {apiError} <button onClick={loadStations}>RETRY</button></div>}
      <section className="panel"><div className="section-title"><h2>Way Stations</h2><span>{loading ? 'Loading...' : `${online}/${stations.length} available`}</span></div>
        {loading ? <div className="empty">Loading station configuration from PostgreSQL...</div> : stations.length === 0 ? <div className="empty">No enabled stations configured</div> : <div className="station-grid">{stations.map(s => <button key={s.id} className={`station ${s.state.toLowerCase()}`} onClick={() => callStation(s)} title={`Call ${s.name} and add to conference`}><strong>{s.id}</strong><small>{s.name}</small><span>● {s.state}</span></button>)}</div>}
      </section>
      <div className="call-actions"><button className="primary" onClick={generalCall} disabled={!stations.length}>GENERAL CALL</button><button onClick={generalCall} disabled={!stations.length}>SECTION CALL</button><button onClick={() => setModal('group')}>GROUP CALL</button><button onClick={() => setModal('add')} disabled={!stations.length}>ADD SUBSCRIBER</button></div>
      <section className="panel"><div className="section-title"><h2>Active Conference</h2><span>{participants.length ? `${participants.length} participant(s)` : 'No active conference'}</span></div>{participants.length === 0 ? <div className="empty">No active participants</div> : <div className="participants">{participants.map(p => <div className="participant" key={p.id}><b>{p.id} — {p.name}</b><span>● {p.state}</span><button onClick={() => toggleMute(p.id)}>{p.state === 'MUTED' ? 'UNMUTE' : 'MUTE'}</button><button onClick={() => remove(p.id)}>REMOVE</button></div>)}</div>}</section>
      <div className="bottom-actions"><button onClick={() => setMessage('Hold control selected')}>Hold</button><button onClick={() => setMessage('Transfer control selected')}>Transfer</button><button onClick={() => setModal('recordings')}>Recordings</button><button onClick={() => setModal('diagnostics')}>Diagnostics</button><button className="emergency" onClick={() => setModal('emergency')}>EMERGENCY</button></div><div className="statusbar">{message}</div>
    </main>
    {modal && <Modal modal={modal} stations={stations} onClose={() => setModal(null)} onAdd={callStation} />}
  </div>;
}

function Modal({ modal, stations, onClose, onAdd }: { modal: string; stations: Station[]; onClose: () => void; onAdd: (s: Station) => void }) {
  if (modal === 'add') return <Overlay title="Add Subscriber" onClose={onClose}><p>Select a registered station.</p><div className="modal-list">{stations.map(s => <button key={s.id} onClick={() => { onAdd(s); onClose(); }}>{s.id} — {s.name}</button>)}</div></Overlay>;
  if (modal === 'directory') return <Overlay title="Directory" onClose={onClose}><div className="modal-list">{stations.map(s => <button key={s.id}>{s.id} — {s.name} — {s.state}</button>)}</div></Overlay>;
  if (modal === 'group') return <Overlay title="Group Call" onClose={onClose}><p>Configured groups will be loaded from PostgreSQL in the backend phase.</p><div className="modal-list"><button onClick={onClose}>SECTION 01 GROUP</button></div></Overlay>;
  if (modal === 'emergency') return <Overlay title="Emergency Call" onClose={onClose}><p><b>TEST MODE:</b> no emergency call is transmitted by this prototype.</p><p>The production implementation will connect this control to the validated emergency communication workflow.</p><button className="emergency" onClick={onClose}>ACTIVATE TEST</button></Overlay>;
  const labels: Record<string,string> = {history:'Call History',settings:'Settings',recordings:'Recordings',diagnostics:'Diagnostics'}; return <Overlay title={labels[modal] || 'TCCS'} onClose={onClose}><p>This function is reserved for the backend integration phase.</p></Overlay>;
}
function Overlay({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) { return <div className="overlay" onClick={onClose}><div className="modal" onClick={e => e.stopPropagation()}><h3>{title}</h3>{children}<div className="modal-footer"><button onClick={onClose}>Close</button></div></div></div>; }
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
