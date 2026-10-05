import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { Integration, NotificationPreferences } from '@/types';
import { getIntegrations, getNotificationPreferences, updateNotificationPreferences } from '@/services/api';
import { Card } from '@/components/ui/Card';
import { Checkbox, Select } from '@/components/ui/Form';
import { Skeleton } from '@/components/ui/Skeleton';
import { Icon } from '@/components/ui/Icon';
import { useToast } from '@/components/ui/Toast';

export function NotificationSettings() {
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [integrations, setIntegrations] = useState<Integration[] | null>(null);
  const toast = useToast();

  useEffect(() => {
    getNotificationPreferences().then(setPrefs);
    getIntegrations().then(setIntegrations);
  }, []);

  const connectedChannels = integrations?.filter((i) => i.category === 'Communication apps' && i.connected) ?? [];

  async function save(next: NotificationPreferences) {
    setPrefs(next);
    await updateNotificationPreferences(next);
    toast('Notification preferences saved.', 'success');
  }

  function toggleChannel(id: string) {
    if (!prefs) return;
    const channels = prefs.channels.includes(id) ? prefs.channels.filter((c) => c !== id) : [...prefs.channels, id];
    save({ ...prefs, channels });
  }

  function toggleEvent(event: string) {
    if (!prefs) return;
    save({ ...prefs, events: prefs.events.map((e) => (e.event === event ? { ...e, enabled: !e.enabled } : e)) });
  }

  function setFrequency(event: string, frequency: 'Immediate' | 'Daily digest') {
    if (!prefs) return;
    save({ ...prefs, events: prefs.events.map((e) => (e.event === event ? { ...e, frequency } : e)) });
  }

  return (
    <div className="max-w-[1440px] mx-auto px-6 py-8 flex flex-col gap-6">
      <div>
        <h1 className="text-[26px] font-bold text-text-primary">Settings</h1>
        <p className="text-[13.5px] text-text-muted mt-1">Notification preferences - which channels get notified, for which events, and how often.</p>
      </div>

      <div className="flex items-start gap-2.5 bg-[#F4F8F7] rounded-md px-4 py-3 text-[12.5px] text-[#133338] leading-snug max-w-[640px]">
        <Icon name="info" size={15} className="text-teal shrink-0 mt-0.5" />
        All notifications appear here in the app - the bell icon - regardless of these settings. Frequency controls only how they are sent to your channels below. There's no per-case mute; these settings apply across every case you raise.
      </div>

      {!prefs || !integrations ? (
        <Skeleton className="h-96 rounded-md max-w-[640px]" />
      ) : (
        <div className="flex flex-col gap-6 max-w-[640px]">
          <Card material="flat" className="p-5 flex flex-col gap-3">
            <span className="text-sm font-bold text-text-primary">Channels</span>
            <p className="text-[12.5px] text-text-muted -mt-1">
              Email is always available. Every other app you've connected appears here too.{' '}
              <Link to="/integrations" className="font-semibold text-carbon-moss hover:underline">
                Manage connections in Integrations
              </Link>
              .
            </p>
            <div className="flex flex-col gap-2">
              <div className="flex flex-col gap-0.5">
                <Checkbox checked={prefs.channels.includes('email')} onChange={() => toggleChannel('email')} label="Email" />
                <span className="text-[11.5px] text-text-faint pl-[27px]">Uses your work email address on the account - not a third-party connection.</span>
              </div>
              {connectedChannels.map((c) => (
                <Checkbox key={c.id} checked={prefs.channels.includes(c.id)} onChange={() => toggleChannel(c.id)} label={c.name} />
              ))}
              {connectedChannels.length === 0 && <p className="text-[13px] text-text-faint">No other communication apps connected yet.</p>}
            </div>
            {prefs.channels.length === 0 && (
              <div className="flex items-start gap-2 bg-[#F4F8F7] rounded-md px-3 py-2.5 text-[12.5px] text-[#133338] leading-snug">
                <Icon name="info" size={14} className="text-teal shrink-0 mt-0.5" />
                No channel selected - a valid choice. Notifications will still appear in the bell, just not pushed anywhere else.
              </div>
            )}
          </Card>

          <Card material="flat" className="p-5 flex flex-col gap-1">
            <span className="text-sm font-bold text-text-primary mb-2">Events</span>
            <div className="divide-y divide-border-hairline">
              {prefs.events.map((e) => (
                <div key={e.event} className="flex items-center justify-between py-3 gap-4">
                  <Checkbox checked={e.enabled} onChange={() => toggleEvent(e.event)} label={<span className="text-[13.5px] font-medium">{e.label}</span>} />
                  <Select
                    value={e.frequency}
                    disabled={!e.enabled}
                    onChange={(ev) => setFrequency(e.event, ev.target.value as 'Immediate' | 'Daily digest')}
                    className="w-[160px]"
                  >
                    <option>Immediate</option>
                    <option>Daily digest</option>
                  </Select>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
