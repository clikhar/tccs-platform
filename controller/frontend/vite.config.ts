import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Keep controller SIP connection user-initiated.
 *
 * Browser microphone access is involved in the controller conference. The
 * previous React mount effect started SIP.js automatically, which could
 * create the WebRTC session before a user gesture and left the UI in
 * REGISTERED after a terminated conference with CONNECT disabled.
 * This compatibility transform keeps the existing UI while making CONNECT
 * explicitly user-initiated and allowing reconnect after termination.
 */
function controllerSessionLifecycle(): Plugin {
  return {
    name: 'tccs-controller-session-lifecycle',
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
        "disabled={controllerStatus!=='DISCONNECTED'&&controllerStatus!=='ERROR'}",
        "disabled={controllerStatus!=='DISCONNECTED'&&controllerStatus!=='ERROR'&&controllerStatus!=='REGISTERED'}",
      );

      return transformed === code ? null : { code: transformed, map: null };
    },
  };
}

export default defineConfig({
  plugins: [react(), controllerSessionLifecycle()],
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
});
