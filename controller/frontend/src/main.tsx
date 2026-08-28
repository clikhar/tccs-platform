import React from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const stations = [
  ['101', 'AJNI CABIN', 'ONLINE'],
  ['102', 'KAMPTEE', 'ONLINE'],
  ['103', 'MARAMJHIRI', 'CALLING'],
  ['104', 'ITARSI', 'OFFLINE'],
  ['105', 'DHARAKHOH', 'ONLINE'],
  ['106', 'WAY STATION 106', 'ONLINE'],
  ['107', 'WAY STATION 107', 'MUTED'],
  ['108', 'WAY STATION 108', 'ONLINE'],
  ['109', 'WAY STATION 109', 'ONLINE'],
  ['110', 'WAY STATION 110', 'ONLINE'],
];

function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div>
          <div className="brand">TCCS</div>
          <div className="subtitle">Train Control Communication System</div>
        </div>
        <div className="top-status">
          <span>SECTION <b>01</b></span>
          <span>CONTROLLER <b>C01</b></span>
          <span className="ok">● SYSTEM NORMAL</span>
        </div>
      </header>
      <main>
        <div className="heading">
          <div><h1>Controller Console</h1><p>Stage 2 prototype</p></div>
          <div className="toolbar"><button>Directory</button><button>Call History</button><button>Settings</button></div>
        </div>
        <section className="panel">
          <div className="section-title"><h2>Way Stations</h2><span>9/10 available</span></div>
          <div className="station-grid">
            {stations.map(([id, name, state]) => <button key={id} className={`station ${state.toLowerCase()}`}>
              <strong>{id}</strong><small>{name}</small><span>● {state}</span>
            </button>)}
          </div>
        </section>
        <div className="call-actions">
          <button className="primary">GENERAL CALL</button><button>SECTION CALL</button><button>GROUP CALL</button><button>ADD SUBSCRIBER</button>
        </div>
        <section className="panel">
          <div className="section-title"><h2>Active Conference</h2><span>No live Asterisk connection</span></div>
          <div className="empty">Conference participants will appear here when backend integration is enabled.</div>
        </section>
        <div className="bottom-actions"><button>Hold</button><button>Transfer</button><button>Recordings</button><button>Diagnostics</button><button className="emergency">EMERGENCY</button></div>
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
