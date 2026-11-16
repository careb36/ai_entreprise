import { BrowserRouter } from 'react-router-dom'
import { AppLayout } from './app/AppLayout'

// The application is split by responsibility (docs/contexts/frontend.md):
// app/AppRoutes.tsx holds the routes and the legacy redirects, app/AppLayout.tsx
// the page around them, components/shell/ the navigation bar and its parts.
// The query client's provider is in main.tsx; the router's is here.
export { LanguageSwitcher } from './components/shell/LanguageSwitcher'
export { sectionForPath } from './components/shell/navLinks'

function App() {
  return (
    <BrowserRouter>
      <AppLayout />
    </BrowserRouter>
  )
}

export default App
