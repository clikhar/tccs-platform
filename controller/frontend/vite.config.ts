import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // The controller UI is accessed through the LAN/reverse proxy.
    // Disable Vite HMR in this deployment so a failed development
    // WebSocket cannot interfere with the SIP.js controller session.
    hmr: false,
  },
});
