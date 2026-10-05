import { useState } from 'react';
import type { Integration } from '@/types';
import { connectIntegration, getIntegrationSiteOptions, getIntegrationToolOptions, testIntegrationConnection, type ConnectPayload } from '@/services/api';
import { Modal } from '@/components/ui/Modal';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { Input, Checkbox } from '@/components/ui/Form';

type DocStep = 'location' | 'authorise' | 'scope' | 'test' | 'confirm';
type CommStep = 'workspace' | 'authorise' | 'destination' | 'test' | 'confirm';
type McpStep = 'server' | 'auth' | 'tools' | 'test' | 'confirm';

const DOC_STEPS: DocStep[] = ['location', 'authorise', 'scope', 'test', 'confirm'];
const COMM_STEPS: CommStep[] = ['workspace', 'authorise', 'destination', 'test', 'confirm'];
const MCP_STEPS: McpStep[] = ['server', 'auth', 'tools', 'test', 'confirm'];

/** The guided connect flow (§8.9 mod, §2) - a prototype with no real authentication. "Authorise"
 * and "Authentication method" are described steps only; nothing here ever collects or stores a
 * password, token or secret. Doubles as the "Manage" flow for an already-connected integration,
 * pre-filled from its current scope, ending in "Save changes" instead of "Connect". */
export function IntegrationConnectModal({ integration, onClose, onConnected }: { integration: Integration | null; onClose: () => void; onConnected: () => void }) {
  const scope = integration?.scope;
  const isManaging = !!integration?.connected;

  const [stepIndex, setStepIndex] = useState(0);
  const [location, setLocation] = useState(scope?.location ?? '');
  const [scopeSites, setScopeSites] = useState<string[]>(scope?.sites ?? []);
  const [workspace, setWorkspace] = useState(scope?.workspace ?? '');
  const [destinationType, setDestinationType] = useState<'channel' | 'dm'>(scope?.destination === 'Direct message' ? 'dm' : 'channel');
  const [destinationValue, setDestinationValue] = useState(scope?.destination && scope.destination !== 'Direct message' ? scope.destination : '');
  const [serverName, setServerName] = useState(scope?.serverName ?? '');
  const [serverUrl, setServerUrl] = useState(scope?.serverUrl ?? '');
  const [tools, setTools] = useState<{ name: string; description: string; enabled: boolean }[]>(
    scope?.tools ?? (integration ? getIntegrationToolOptions(integration.id).map((t) => ({ ...t, enabled: true })) : []),
  );
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const [testConfirmedArrived, setTestConfirmedArrived] = useState(false);
  const [connecting, setConnecting] = useState(false);

  if (!integration) return null;
  // Closures below capture this const, not the parameter - TS narrows a parameter's null check
  // only for the rest of this scope, not inside nested function bodies.
  const current = integration;

  const steps = current.category === 'Document sources' ? DOC_STEPS : current.category === 'Communication apps' ? COMM_STEPS : MCP_STEPS;
  const step = steps[stepIndex];

  function next() {
    setStepIndex((i) => Math.min(i + 1, steps.length - 1));
  }
  function back() {
    setStepIndex((i) => Math.max(i - 1, 0));
  }

  function buildPayload(): ConnectPayload {
    return {
      location,
      scopeSites,
      workspace,
      destination: destinationType === 'dm' ? 'Direct message' : destinationValue || undefined,
      serverName,
      serverUrl,
      tools,
    };
  }

  async function runTest() {
    setTesting(true);
    const result = await testIntegrationConnection(current.id, buildPayload());
    setTestResult(result);
    setTestConfirmedArrived(false);
    setTesting(false);
  }

  async function confirmConnect() {
    setConnecting(true);
    await connectIntegration(current.id, buildPayload(), testResult ?? { ok: false, detail: 'Not tested.' });
    setConnecting(false);
    onConnected();
    onClose();
  }

  function toggleSite(site: string) {
    setScopeSites((s) => (s.includes(site) ? s.filter((x) => x !== site) : [...s, site]));
  }

  function toggleTool(name: string) {
    setTools((ts) => ts.map((t) => (t.name === name ? { ...t, enabled: !t.enabled } : t)));
  }

  const canGoNext = (() => {
    if (step === 'location') return location.trim().length > 0;
    if (step === 'workspace') return workspace.trim().length > 0;
    if (step === 'server') return serverName.trim().length > 0 && serverUrl.trim().length > 0;
    if (step === 'scope') return scopeSites.length > 0;
    if (step === 'destination') return destinationType === 'dm' || destinationValue.trim().length > 0;
    if (step === 'tools') return tools.some((t) => t.enabled);
    return true;
  })();

  return (
    <Modal open onClose={onClose} title={`${isManaging ? 'Manage' : 'Connect'} ${integration.name}`} width={520}>
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-1.5">
          {steps.map((s, i) => (
            <div key={s} className={`h-1.5 flex-1 rounded-full ${i <= stepIndex ? 'bg-carbon-moss' : 'bg-border'}`} />
          ))}
        </div>

        <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-3.5 py-2.5 text-[11.5px] text-[#133338] leading-snug">
          <Icon name="lock" size={13} className="text-teal shrink-0 mt-0.5" />
          Prototype flow - no real authentication happens here. No password, token or secret is collected or stored at any step.
        </div>

        {step === 'location' && (
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-semibold text-[#15393e]">Site or space URL</label>
            <Input
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder={integration.id === 'sharepoint' ? 'https://contoso.sharepoint.com/sites/...' : 'https://contoso.atlassian.net/wiki/spaces/...'}
            />
          </div>
        )}

        {step === 'workspace' && (
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-semibold text-[#15393e]">Workspace or tenant</label>
            <Input value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder={integration.id === 'slack' ? 'contoso.slack.com' : 'contoso.onmicrosoft.com'} />
          </div>
        )}

        {step === 'server' && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <label className="text-[13px] font-semibold text-[#15393e]">Server name</label>
              <Input value={serverName} onChange={(e) => setServerName(e.target.value)} placeholder="Data Catalog MCP" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-[13px] font-semibold text-[#15393e]">Endpoint URL</label>
              <Input value={serverUrl} onChange={(e) => setServerUrl(e.target.value)} placeholder="https://mcp.internal.contoso.com/data-catalog" />
            </div>
          </div>
        )}

        {step === 'authorise' && (
          <div className="flex flex-col gap-2">
            <p className="text-[13.5px] text-text-primary leading-relaxed">You'd be redirected to sign in and grant read access. No credentials are collected in this flow.</p>
            <div className="border border-border-strong rounded-md px-3.5 py-3 flex items-center gap-2.5 text-[12.5px] text-text-muted bg-surface-tertiary">
              <Icon name="external-link" size={14} />
              Sign-in and consent screen (described step only)
            </div>
          </div>
        )}

        {step === 'auth' && (
          <div className="flex flex-col gap-2">
            <p className="text-[13.5px] text-text-primary leading-relaxed">This server authenticates with an API key, held server-side once connected. No key is collected here.</p>
            <div className="border border-border-strong rounded-md px-3.5 py-3 flex items-center gap-2.5 text-[12.5px] text-text-muted bg-surface-tertiary">
              <Icon name="lock" size={14} />
              Authentication handshake (described step only)
            </div>
          </div>
        )}

        {step === 'scope' && (
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-semibold text-[#15393e]">Which {integration.id === 'sharepoint' ? 'sites' : 'spaces'} may the Extraction Agent read?</label>
            <div className="flex flex-col gap-1 border border-border-strong rounded-md p-2.5 max-h-40 overflow-y-auto">
              {getIntegrationSiteOptions(integration.id).map((site) => (
                <Checkbox key={site} checked={scopeSites.includes(site)} onChange={() => toggleSite(site)} label={site} />
              ))}
            </div>
          </div>
        )}

        {step === 'destination' && (
          <div className="flex flex-col gap-3">
            <div className="flex gap-2">
              <Button size="sm" variant={destinationType === 'channel' ? 'primary' : 'ghost'} onClick={() => setDestinationType('channel')}>
                Channel
              </Button>
              <Button size="sm" variant={destinationType === 'dm' ? 'primary' : 'ghost'} onClick={() => setDestinationType('dm')}>
                Direct message
              </Button>
            </div>
            {destinationType === 'channel' && (
              <Input value={destinationValue} onChange={(e) => setDestinationValue(e.target.value)} placeholder={integration.id === 'slack' ? '#risk-ops' : 'Risk & Compliance'} />
            )}
            {destinationType === 'dm' && <p className="text-[12.5px] text-text-muted">Sent directly to your own account.</p>}
          </div>
        )}

        {step === 'tools' && (
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-semibold text-[#15393e]">Tools this server offers</label>
            <div className="flex flex-col gap-2.5 border border-border-strong rounded-md p-2.5">
              {tools.map((t) => (
                <div key={t.name} className="flex items-start justify-between gap-3">
                  <div className="flex flex-col">
                    <span className="text-[13px] font-mono font-semibold text-text-primary">{t.name}</span>
                    <span className="text-[12px] text-text-muted">{t.description}</span>
                  </div>
                  <Checkbox checked={t.enabled} onChange={() => toggleTool(t.name)} />
                </div>
              ))}
            </div>
          </div>
        )}

        {step === 'test' && (
          <div className="flex flex-col gap-3">
            {!testResult ? (
              <Button variant="secondary" onClick={runTest} loading={testing} className="self-start">
                {integration.category === 'Communication apps' ? 'Send test notification' : 'Run test'}
              </Button>
            ) : (
              <>
                <div className={`flex items-start gap-2.5 rounded-md px-3.5 py-3 ${testResult.ok ? 'bg-[#EAF6F1]' : 'bg-danger-bg'}`}>
                  <Icon name={testResult.ok ? 'check' : 'warning'} size={16} className={testResult.ok ? 'text-teal shrink-0 mt-0.5' : 'text-danger shrink-0 mt-0.5'} />
                  <p className={`text-[13px] leading-relaxed ${testResult.ok ? 'text-[#133338]' : 'text-danger'}`}>{testResult.detail}</p>
                </div>
                {integration.category === 'Communication apps' && testResult.ok && (
                  <div className="flex items-center gap-2">
                    <span className="text-[12.5px] text-text-muted">Did the test notification arrive?</span>
                    <Button size="sm" variant={testConfirmedArrived ? 'primary' : 'secondary'} onClick={() => setTestConfirmedArrived(true)}>
                      {testConfirmedArrived ? 'Confirmed' : 'Yes, it arrived'}
                    </Button>
                  </div>
                )}
                {!testResult.ok && <p className="text-[12px] text-text-faint">You can complete the connection anyway - the failed test stays noted on the integration.</p>}
                <Button size="sm" variant="ghost" className="self-start" onClick={runTest} loading={testing}>
                  Run again
                </Button>
              </>
            )}
          </div>
        )}

        {step === 'confirm' && (
          <div className="flex flex-col gap-2">
            <span className="text-[13px] font-semibold text-text-primary">Ready to {isManaging ? 'save' : 'connect'}</span>
            <div className="text-[12.5px] text-text-secondary flex flex-col gap-1">
              {integration.category === 'Document sources' && (
                <>
                  <span>Location: {location || '-'}</span>
                  <span>Scope: {scopeSites.join(', ') || '-'}</span>
                </>
              )}
              {integration.category === 'Communication apps' && (
                <>
                  <span>Workspace: {workspace || '-'}</span>
                  <span>Destination: {destinationType === 'dm' ? 'Direct message' : destinationValue || '-'}</span>
                </>
              )}
              {integration.category === 'MCP servers / apps' && (
                <>
                  <span>
                    Server: {serverName || '-'} ({serverUrl || '-'})
                  </span>
                  <span>Tools enabled: {tools.filter((t) => t.enabled).map((t) => t.name).join(', ') || 'none'}</span>
                </>
              )}
              {testResult && (
                <span className={testResult.ok ? 'text-teal' : 'text-danger'}>
                  Last test: {testResult.ok ? 'passed' : 'failed'} - {testResult.detail}
                </span>
              )}
            </div>
          </div>
        )}

        <div className="flex justify-between pt-1">
          <Button variant="ghost" onClick={stepIndex === 0 ? onClose : back}>
            {stepIndex === 0 ? 'Cancel' : 'Back'}
          </Button>
          {step === 'confirm' ? (
            <Button variant="primary" onClick={confirmConnect} loading={connecting}>
              {isManaging ? 'Save changes' : 'Connect'}
            </Button>
          ) : (
            <Button variant="primary" onClick={next} disabled={!canGoNext}>
              Next
            </Button>
          )}
        </div>
      </div>
    </Modal>
  );
}
