import { Link, NavLink, Route, Routes } from 'react-router-dom'
import Matrix from './pages/Matrix.jsx'
import Service from './pages/Service.jsx'
import Status from './pages/Status.jsx'

export default function App() {
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
