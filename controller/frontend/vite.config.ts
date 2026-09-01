import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
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
