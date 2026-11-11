import { useEffect, useState } from 'react'
import Home from './Home.jsx'
import IncidentPage from './IncidentPage.jsx'

function useRoute() {
  const read = () => (window.location.hash.match(/^#\/incident\/([a-f0-9]+)/) || [])[1] || null
  const [id, setId] = useState(read)
  useEffect(() => {
    const on = () => setId(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return id
}

export function Logo({ size = 26 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true">
      <rect x="6" y="12" width="52" height="40" rx="6" fill="var(--recorder)" />
      <path d="M6 22h52M6 42h52" stroke="var(--panel)" strokeWidth="3" />
      <circle cx="32" cy="32" r="6" fill="var(--panel)" />
    </svg>
  )
}

export default function App() {
  const id = useRoute()
  return (
    <div className="app">
      <header className="top">
        <a className="brand" href="#"><Logo /><span>BlackBox</span></a>
        <nav><a href="#">Recordings</a></nav>
      </header>
      {id ? <IncidentPage key={id} id={id} /> : <Home />}
    </div>
  )
}
