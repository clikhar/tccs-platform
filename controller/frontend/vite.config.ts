import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Keep controller SIP connection user-initiated and make the conference
 * session lifecycle visible in the existing UI.
 *
 * The browser must register first and then place the conference INVITE from
 * the CONNECT button. A pending SIP INVITE must not look like a successful
 * registration, otherwise the operator cannot distinguish REGISTERED from
 * actually being in SECTION01.
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
  plugins: [react(), controllerSessionLifecycle()],
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
});
