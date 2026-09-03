import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'tccs-master-actions',
      transformIndexHtml(html) {
        if (!html.includes('<title>TCCS Master Administration</title>')) return html;
        return html.replace('</head>', '  <script src="/master-actions.js"></script>\n</head>');
      },
    },
  ],
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
