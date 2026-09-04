import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { applyTheme, readStoredTheme } from './theme.ts'
import ConsoleShell from './ConsoleShell.tsx'

// 渲染前把主题放到 <html> 上，避免首帧按默认深色闪一下
applyTheme(readStoredTheme())

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConsoleShell />
  </StrictMode>,
)
