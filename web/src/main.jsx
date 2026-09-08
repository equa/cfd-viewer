import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MantineProvider, createTheme } from '@mantine/core'
import '@mantine/core/styles.css'
import './index.css'
import App from './App.jsx'
import { urlOptions } from './api.js'

/*
 * Mantine v7+ ships plain CSS modules rather than Emotion, so its stylesheet
 * has to be imported explicitly (above) -- omit it and every component renders
 * unstyled, which looks like a build failure but is not.
 *
 * The colour scheme is FORCED from ?theme=, not chosen here: the cockpit embeds
 * this viewer in an iframe and passes its own theme so the two match. Letting
 * the viewer pick its own would let the two disagree.
 */

const theme = createTheme({
  fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif',
  fontFamilyMonospace: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  primaryColor: 'blue',
  defaultRadius: 'sm',
  // The panels are dense by nature -- six tools' worth of controls in a 320 px
  // pane -- so shrink the default control sizes rather than fighting them per
  // component.
  components: {
    Switch: { defaultProps: { size: 'xs' } },
    Button: { defaultProps: { size: 'xs' } },
  },
})

const { theme: scheme } = urlOptions()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <MantineProvider theme={theme} forceColorScheme={scheme}>
      <App />
    </MantineProvider>
  </StrictMode>,
)

// Signal for tests/check_client.py: the module graph evaluated and React mounted.
window.__vizReady = true
