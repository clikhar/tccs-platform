import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const tccsCallStatusLatency = () => ({
  name: 'tccs-call-status-latency',
  transform(code: string, id: string) {
    if (!id.endsWith('/src/main.tsx')) return null;

    // Asterisk status is polled frequently. Keep one stable ref containing
    // the latest optimistic CALLING state so polling never captures a stale
    // React closure and removes a newly originated station call.
    let transformed = code.replace(
      'const[callingIds,setCallingIds]=useState<Set<string>>(new Set());',
      'const[callingIds,setCallingIds]=useState<Set<string>>(new Set());const callingIdsRef=useRef(callingIds);callingIdsRef.current=callingIds;'
    );

    // Use the latest calling state everywhere in the polling/call handlers.
    transformed = transformed.replace(/callingIds\.has\(/g, 'callingIdsRef.current.has(');

    // Poll continuously without restarting the interval whenever callingIds
    // changes. This prevents overlapping polling loops during call setup.
    transformed = transformed.replace(
      '[stations.length,callingIds]',
      '[stations.length]'
    );
    transformed = transformed.replace(
      'setInterval(refreshEndpointStatus,3000)',
      'setInterval(refreshEndpointStatus,500)'
    );

    // Keep the optimistic CALLING participant visible until Asterisk has had
    // time to transition from originate/ringing into the conference.
    transformed = transformed.replace(
      "finally{setCallingIds(current=>{const next=new Set(current);next.delete(station.id);return next;});}",
      "finally{window.setTimeout(()=>setCallingIds(current=>{const next=new Set(current);next.delete(station.id);return next;}),15000);}"
    );

    return transformed === code ? null : { code: transformed, map: null };
  },
});

export default defineConfig({
  plugins: [tccsCallStatusLatency(), react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // The controller UI is accessed through HTTPS on 192.168.1.21:443.
    // There is no reliable Vite HMR WebSocket proxy in that deployment,
    // so disable HMR rather than repeatedly reconnecting and disrupting
    // the page/SIP.js session.
    hmr: false,
  },
});
