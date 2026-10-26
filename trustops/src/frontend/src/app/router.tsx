/**
 * Route tree (design C §1.2, ADR-19). Every screen is lazy-loaded. Guards: RequireAuth for any
 * signed-in route; RequireRole per persona subtree (wrong role -> own home, no hint); the monitor
 * answers 403 to the PO. Tabs are child routes so the URL is the state (design C §1.5 rule 3).
 */
import type { ComponentType } from 'react';
import { Navigate, createBrowserRouter, type RouteObject } from 'react-router-dom';
import { PageSkeleton } from '@/components/layout/PageSkeleton';
import { NotFound } from '@/components/layout/NotFound';
import { RouteError } from '@/components/layout/RouteError';
import { HomeRedirect } from './guards/HomeRedirect';
import { RequireAuth } from './guards/RequireAuth';
import { RequireRole } from './guards/RequireRole';
import { AppShell } from './layouts/AppShell';
import { AuthLayout } from './layouts/AuthLayout';
import { MonitorLayout } from './layouts/MonitorLayout';

type Mod = Record<string, unknown>;

/** Lazy route module: `lazy(() => import('...'), 'ExportName')`. */
function lazy(load: () => Promise<Mod>, name: string) {
  return async () => {
    const mod = await load();
    return { Component: mod[name] as ComponentType };
  };
}

export const routes: RouteObject[] = [
  {
    path: '/',
    HydrateFallback: () => <PageSkeleton />,
    errorElement: <RouteError />,
    children: [
      {
        element: <AuthLayout />,
        children: [{ path: 'login', lazy: lazy(() => import('@/features/auth/LoginPage'), 'LoginPage') }],
      },
      {
        element: <RequireAuth />,
        children: [
          // `/` is no persona's page: it sends a signed-in person to their own home (the PO's is /po).
          { index: true, element: <HomeRedirect /> },
          {
            path: 'monitor/:caseId',
            element: <RequireRole roles={['fcrm_analyst', 'fcrm_committee', 'fcrm_observer']} onDenied="forbidden" />,
            children: [{ element: <MonitorLayout />, children: [{ index: true, lazy: lazy(() => import('@/features/monitor/MonitorPage'), 'MonitorPage') }] }],
          },
          {
            element: <AppShell />,
            children: [
              // ---------------------------------------------------------------- product owner
              {
                element: <RequireRole roles={['fcrm_po']} />,
                children: [
                  { path: 'po', lazy: lazy(() => import('@/features/po/Dashboard'), 'PODashboard') },
                  { path: 'queue', lazy: lazy(() => import('@/features/po/MyQueue'), 'MyQueue') },
                  { path: 'case/new', lazy: lazy(() => import('@/features/po/NewCase'), 'NewCase') },
                  {
                    path: 'case/:id',
                    lazy: lazy(() => import('@/features/po/CaseLayout'), 'POCaseLayout'),
                    children: [
                      { index: true, element: <Navigate to="overview" replace /> },
                      { path: 'overview', lazy: lazy(() => import('@/features/po/tabs/Overview'), 'OverviewTab') },
                      { path: 'documents', lazy: lazy(() => import('@/features/po/tabs/Documents'), 'DocumentsTab') },
                      { path: 'review', lazy: lazy(() => import('@/features/po/tabs/ReviewSubmit'), 'ReviewSubmitTab') },
                      { path: 'inbox', lazy: lazy(() => import('@/features/po/tabs/Inbox'), 'POInboxTab') },
                      { path: 'activity', lazy: lazy(() => import('@/features/po/tabs/Activity'), 'POActivityTab') },
                      { path: 'conditions', lazy: lazy(() => import('@/features/po/tabs/Conditions'), 'ConditionsTab') },
                    ],
                  },
                  { path: 'integrations', lazy: lazy(() => import('@/features/po/Integrations'), 'Integrations') },
                ],
              },
              // ---------------------------------------------------------------- analyst
              {
                path: 'analyst',
                element: <RequireRole roles={['fcrm_analyst']} />,
                children: [
                  { index: true, lazy: lazy(() => import('@/features/analyst/Dashboard'), 'AnalystDashboard') },
                  { path: 'queue', lazy: lazy(() => import('@/features/analyst/Queue'), 'AnalystQueue') },
                  {
                    path: 'case/:id',
                    lazy: lazy(() => import('@/features/analyst/WorkbenchLayout'), 'WorkbenchLayout'),
                    children: [
                      { index: true, element: <Navigate to="summary" replace /> },
                      { path: 'summary', lazy: lazy(() => import('@/features/analyst/tabs/Summary'), 'SummaryTab') },
                      { path: 'submission', lazy: lazy(() => import('@/features/analyst/tabs/Submission'), 'SubmissionTab') },
                      { path: 'inbox', lazy: lazy(() => import('@/features/analyst/tabs/Inbox'), 'AnalystInboxTab') },
                      { path: 'policy', lazy: lazy(() => import('@/features/analyst/tabs/Policy'), 'PolicyTab') },
                      { path: 'risk-controls', lazy: lazy(() => import('@/features/analyst/tabs/RiskControls'), 'RiskControlsTab') },
                      { path: 'recommendation', lazy: lazy(() => import('@/features/analyst/tabs/Recommendation'), 'RecommendationTab') },
                      { path: 'handoff', lazy: lazy(() => import('@/features/analyst/tabs/Handoff'), 'HandoffTab') },
                      { path: 'activity', lazy: lazy(() => import('@/features/analyst/tabs/Activity'), 'AnalystActivityTab') },
                    ],
                  },
                  {
                    path: 'assurance',
                    lazy: lazy(() => import('@/features/analyst/assurance/AssuranceLayout'), 'AssuranceLayout'),
                    children: [
                      { index: true, element: <Navigate to="ask" replace /> },
                      { path: 'ask', lazy: lazy(() => import('@/features/analyst/assurance/AskTab'), 'AskTab') },
                      { path: 'overrides', lazy: lazy(() => import('@/features/analyst/assurance/OverridesTab'), 'OverridesTab') },
                      { path: 'watchlist', lazy: lazy(() => import('@/features/analyst/assurance/WatchlistTab'), 'WatchlistTab') },
                      { path: 'agent-quality', lazy: lazy(() => import('@/features/analyst/assurance/AgentQualityTab'), 'AgentQualityTab') },
                    ],
                  },
                  {
                    path: 'config',
                    lazy: lazy(() => import('@/features/analyst/config/ConfigLayout'), 'ConfigLayout'),
                    children: [
                      { index: true, element: <Navigate to="field-sets" replace /> },
                      { path: ':section', lazy: lazy(() => import('@/features/analyst/config/ConfigSectionPage'), 'ConfigSectionPage') },
                    ],
                  },
                ],
              },
              // ---------------------------------------------------------------- committee
              {
                path: 'committee',
                element: <RequireRole roles={['fcrm_committee']} />,
                children: [
                  { index: true, lazy: lazy(() => import('@/features/committee/Queue'), 'CommitteeQueue') },
                  {
                    path: 'case/:id',
                    lazy: lazy(() => import('@/features/committee/CaseLayout'), 'CommitteeCaseLayout'),
                    children: [
                      { index: true, element: <Navigate to="pack" replace /> },
                      { path: 'pack', lazy: lazy(() => import('@/features/committee/tabs/Pack'), 'PackTab') },
                      { path: 'submission', lazy: lazy(() => import('@/features/committee/tabs/Submission'), 'CommitteeSubmissionTab') },
                      { path: 'inbox', lazy: lazy(() => import('@/features/committee/tabs/Inbox'), 'CommitteeInboxTab') },
                      { path: 'vote', lazy: lazy(() => import('@/features/committee/tabs/Vote'), 'VoteTab') },
                    ],
                  },
                ],
              },
              { path: '*', element: <NotFound /> },
            ],
          },
        ],
      },
    ],
  },
];

export function createAppRouter() {
  return createBrowserRouter(routes);
}
