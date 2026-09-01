import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Keep controller SIP connection user-initiated and make the conference
 * session lifecycle visible in the existing UI.
 *
 * This transform must run before @vitejs/plugin-react so it operates on the
 * original TSX source rather than already-transformed JavaScript.
 */
function controllerSessionLifecycle(): Plugin {
  return {
    name: 'tccs-controller-session-lifecycle',
    enforce: 'pre',
    transform(code, id) {
      if (!id.endsWith('/src/main.tsx')) return null;

      let transformed = code;
      transformed = transformed.replace(
        "useState<ControllerStatus>('CONNECTING')",
        "useState<ControllerStatus>('DISCONNECTED')",
      );
      transformed = transformed.replace(
        'useEffect(()=>{connectController();return()=>{disconnectController();if(audioRef.current){audioRef.current.remove();audioRef.current=null;}};},[]);',
        'useEffect(()=>{return()=>{disconnectController();if(audioRef.current){audioRef.current.remove();audioRef.current=null;}};},[]);',
      );
      transformed = transformed.replace(
        "setControllerStatus('REGISTERED');setMessage('Controller 9999 registered');await joinControllerConference(ua);",
        "setControllerStatus('CONNECTING');setMessage('Registered • joining SECTION 01...');await joinControllerConference(ua);",
      );
      transformed = transformed.replace(
        "disabled={controllerStatus!=='DISCONNECTED'&&controllerStatus!=='ERROR'}",
        "disabled={controllerStatus!=='DISCONNECTED'&&controllerStatus!=='ERROR'&&controllerStatus!=='REGISTERED'}",
      );

      return transformed === code ? null : { code: transformed, map: null };
    },
  };
}

export default defineConfig({
  plugins: [controllerSessionLifecycle(), react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    hmr: {
      protocol: 'wss',
      host: '192.168.1.21',
      clientPort: 443,
    },
  },
});
