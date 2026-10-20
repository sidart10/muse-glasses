import { createRoot } from 'react-dom/client';
import { App as ToolkitApp } from '@wearables-ui-toolkit/mrbd';
import { MuseApp, SafeBoundary } from './MuseApp';
import { captureToken, unlockAudioOnFirstInput } from './muse';
import './muse.css';

(window as unknown as { __bootJs?: number }).__bootJs = performance.now();
captureToken();
unlockAudioOnFirstInput();
createRoot(document.getElementById('root')!).render(
  <ToolkitApp>
    <SafeBoundary>
      <MuseApp />
    </SafeBoundary>
  </ToolkitApp>,
);

// Keep the app stored on the glasses so later launches open instantly (see public/sw.js).
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => {}); });
}
