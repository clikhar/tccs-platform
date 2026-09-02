import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const tccsCallStatusLatency = () => ({
  name: 'tccs-call-status-latency',
  transform(code: string, id: string) {
    if (!id.endsWith('/src/main.tsx')) return null;

    // Keep the controller UI responsive while Asterisk is transitioning
    // from originate -> ringing -> conference. The UI already has an
    // optimistic CALLING participant; retain it long enough for Asterisk
    // state to become authoritative.
    let transformed = code.replace(
      'setInterval(refreshEndpointStatus,3000)',
      'setInterval(refreshEndpointStatus,500)'
    );

    transformed = transformed.replace(
      "else if (callingIds.has(station.id) && (ringing || inCall)) {",
      "else if (callingIds.has(station.id)) {"
    );

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
