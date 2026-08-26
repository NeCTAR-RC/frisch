import { Link, NavLink, Route, Routes } from 'react-router-dom'
import Matrix from './pages/Matrix.jsx'
import Service from './pages/Service.jsx'
import Status from './pages/Status.jsx'
import { useTheme } from './useTheme.js'

export default function App() {
  const { theme, toggleTheme } = useTheme()

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          frisch
        </Link>
        <nav>
          <NavLink to="/" end>
            Matrix
          </NavLink>
          <NavLink to="/status">Status</NavLink>
        </nav>
        <button
          type="button"
          className="theme-toggle"
          onClick={toggleTheme}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
        >
          {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Matrix />} />
          <Route path="/services/:name" element={<Service />} />
          <Route path="/status" element={<Status />} />
        </Routes>
      </main>
    </div>
  )
}

function SunIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  )
}
