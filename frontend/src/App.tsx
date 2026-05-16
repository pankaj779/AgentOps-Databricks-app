import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { AppShell } from '@/components/AppShell'
import { WorkspaceSelectionProvider } from '@/context/WorkspaceSelectionContext'
import { TraceDetailPage } from '@/views/TraceDetailPage'

export default function App() {
  return (
    <BrowserRouter>
      <WorkspaceSelectionProvider>
        <Routes>
          <Route path="/trace/:requestId" element={<TraceDetailPage />} />
          <Route path="/*" element={<AppShell />} />
        </Routes>
      </WorkspaceSelectionProvider>
    </BrowserRouter>
  )
}
