import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import 'vis-timeline/styles/vis-timeline-graph2d.min.css'
import './styles.css'

createRoot(document.getElementById('root')).render(<StrictMode><App /></StrictMode>)
