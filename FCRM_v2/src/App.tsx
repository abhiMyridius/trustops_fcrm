import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { AppLayout } from '@/components/layout/AppLayout';
import { ToastProvider } from '@/components/ui/Toast';
import { PersonaProvider } from '@/context/PersonaContext';
import { Dashboard } from '@/pages/Dashboard';
import { Queue } from '@/pages/Queue';
import { CaseDetailPage } from '@/pages/CaseDetail/CaseDetailPage';
import { IntakeChat } from '@/pages/NewCase/IntakeChat';
import { Templates } from '@/pages/Templates';
import { Integrations } from '@/pages/Integrations';
import { AIConfiguration } from '@/pages/AIConfiguration';
import { NotificationSettings } from '@/pages/NotificationSettings';
import { AnalystDashboard } from '@/pages/analyst/AnalystDashboard';
import { AnalystQueue } from '@/pages/analyst/AnalystQueue';
import { CaseWorkbenchPage } from '@/pages/analyst/CaseWorkbench/CaseWorkbenchPage';
import { ConfigPage } from '@/pages/analyst/Config/ConfigPage';
import { AssurancePage } from '@/pages/analyst/Assurance/AssurancePage';
import { CommitteeQueue } from '@/pages/committee/CommitteeQueue';
import { CommitteeCasePage } from '@/pages/committee/CommitteeCasePage';

export default function App() {
  return (
    <ToastProvider>
      <PersonaProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppLayout />}>
              <Route path="/" element={<Dashboard />} />
              <Route path="/queue" element={<Queue />} />
              <Route path="/case/:id" element={<CaseDetailPage />} />
              <Route path="/case/:id/intake" element={<IntakeChat />} />
              <Route path="/templates" element={<Templates />} />
              <Route path="/integrations" element={<Integrations />} />
              <Route path="/ai-configuration" element={<AIConfiguration />} />
              <Route path="/settings/notifications" element={<NotificationSettings />} />

              <Route path="/analyst" element={<AnalystDashboard />} />
              <Route path="/analyst/queue" element={<AnalystQueue />} />
              <Route path="/analyst/case/:id" element={<CaseWorkbenchPage />} />
              <Route path="/analyst/assurance" element={<AssurancePage />} />
              <Route path="/analyst/config" element={<ConfigPage />} />

              <Route path="/committee" element={<CommitteeQueue />} />
              <Route path="/committee/case/:id" element={<CommitteeCasePage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </PersonaProvider>
    </ToastProvider>
  );
}
