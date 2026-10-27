import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import './App.css'
import './chat/chat.css'
import './judge/judge.css'
import './question/question.css'
import './results/results.css'
import './share/share.css'
import './sql/sql.css'
import { shareToken } from './share/share'
import { SharedPage } from './share/SharedPage'

// "/share/<token>": the read-only page of a shared link (X3), without WebSocket
const token = shareToken(window.location.pathname)

createRoot(document.getElementById('root')!).render(
  <StrictMode>{token !== null ? <SharedPage token={token} /> : <App />}</StrictMode>,
)
