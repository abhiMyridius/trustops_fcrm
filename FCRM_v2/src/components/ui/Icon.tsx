// Stroke-based icon set - 24x24 grid, 1.6px weight, rounded caps (Design System §Icons).
import type { ReactNode } from 'react';

export type IconName =
  | 'dashboard'
  | 'queue'
  | 'new-case'
  | 'templates'
  | 'integrations'
  | 'ai-config'
  | 'bell'
  | 'chevron-down'
  | 'chevron-right'
  | 'chevron-left'
  | 'close'
  | 'check'
  | 'lock'
  | 'upload'
  | 'attachment'
  | 'external-link'
  | 'search'
  | 'filter'
  | 'calendar'
  | 'trash'
  | 'plus'
  | 'arrow-right'
  | 'warning'
  | 'info'
  | 'user'
  | 'sparkles'
  | 'history'
  | 'download'
  | 'edit'
  | 'reply'
  | 'inbox'
  | 'document'
  | 'folder'
  | 'grip'
  | 'refresh'
  | 'send'
  | 'more'
  | 'arrow-left'
  | 'settings'
  | 'type-text'
  | 'type-longtext'
  | 'type-number'
  | 'type-multiselect'
  | 'expand'
  | 'collapse'
  | 'users';

const paths: Record<IconName, ReactNode> = {
  dashboard: (
    <>
      <rect x="3" y="3" width="8" height="8" rx="1.5" />
      <rect x="13" y="3" width="8" height="8" rx="1.5" />
      <rect x="3" y="13" width="8" height="8" rx="1.5" />
      <rect x="13" y="13" width="8" height="8" rx="1.5" />
    </>
  ),
  queue: (
    <>
      <path d="M4 13h4l2 3h4l2-3h4" />
      <path d="M6 4h12l2 9v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-6l2-9z" />
    </>
  ),
  'new-case': (
    <>
      <path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M15 3v4h4" />
      <path d="M9 14h6" />
      <path d="M12 11v6" />
    </>
  ),
  templates: (
    <>
      <path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M15 3v4h4" />
    </>
  ),
  integrations: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3c2.5 2.5 3.5 6 3.5 9s-1 6.5-3.5 9c-2.5-2.5-3.5-6-3.5-9s1-6.5 3.5-9z" />
    </>
  ),
  'ai-config': (
    <>
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="6" cy="18" r="2.2" />
      <circle cx="18" cy="12" r="2.2" />
      <path d="M8 7l8 4" />
      <path d="M8 17l8-4" />
    </>
  ),
  bell: (
    <>
      <path d="M6 10a6 6 0 1 1 12 0c0 4 1.5 5.5 1.5 5.5H4.5S6 14 6 10z" />
      <path d="M9.5 18.5a2.5 2.5 0 0 0 5 0" />
    </>
  ),
  'chevron-down': <path d="M6 9l6 6 6-6" />,
  'chevron-right': <path d="M9 6l6 6-6 6" />,
  'chevron-left': <path d="M15 6l-6 6 6 6" />,
  close: (
    <>
      <path d="M6 6l12 12" />
      <path d="M18 6L6 18" />
    </>
  ),
  check: <path d="M5 12.5l5 5L19 7" />,
  lock: <path d="M12 3l7 3v6c0 5-3.5 7.5-7 9-3.5-1.5-7-4-7-9V6l7-3z" />,
  upload: (
    <>
      <path d="M12 16V4" />
      <path d="M7 9l5-5 5 5" />
      <path d="M5 16v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" />
    </>
  ),
  attachment: (
    <>
      <path d="M17 8l-6.5 6.5a3 3 0 1 0 4.24 4.24L21 12.5" />
      <path d="M17 8l-2-2-6.5 6.5a3 3 0 0 0 0 4.24" />
    </>
  ),
  'external-link': (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4L10 14" />
      <path d="M18 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h6" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </>
  ),
  filter: <path d="M4 5h16M7 12h10M10 19h4" />,
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
      <path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  'arrow-right': <path d="M4 12h16M13 5l7 7-7 7" />,
  warning: (
    <>
      <path d="M12 3l9 16H3L12 3z" />
      <path d="M12 10v4" />
      <path d="M12 17.5v.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6" />
      <path d="M12 7.5v.01" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-4 3.5-6 8-6s8 2 8 6" />
    </>
  ),
  sparkles: (
    <>
      <path d="M12 3l1.6 4.4L18 9l-4.4 1.6L12 15l-1.6-4.4L6 9l4.4-1.6L12 3z" />
      <path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z" />
    </>
  ),
  history: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l4 2" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v12" />
      <path d="M7 11l5 5 5-5" />
      <path d="M5 20h14" />
    </>
  ),
  edit: (
    <>
      <path d="M4 20l1-4L16 5l3 3L8 19l-4 1z" />
      <path d="M14 7l3 3" />
    </>
  ),
  reply: <path d="M9 10L4 15l5 5M4 15h11a5 5 0 0 0 5-5V6" />,
  inbox: (
    <>
      <path d="M4 13h4l2 3h4l2-3h4" />
      <path d="M6 4h12l2 9v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-6l2-9z" />
    </>
  ),
  document: (
    <>
      <path d="M6 3h9l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M15 3v4h4" />
    </>
  ),
  folder: <path d="M4 6a1 1 0 0 1 1-1h4l2 2h8a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6z" />,
  grip: (
    <>
      <circle cx="9" cy="6" r="1.3" />
      <circle cx="15" cy="6" r="1.3" />
      <circle cx="9" cy="12" r="1.3" />
      <circle cx="15" cy="12" r="1.3" />
      <circle cx="9" cy="18" r="1.3" />
      <circle cx="15" cy="18" r="1.3" />
    </>
  ),
  refresh: (
    <>
      <path d="M4 4v6h6" />
      <path d="M20 20v-6h-6" />
      <path d="M5 15a8 8 0 0 0 14.2 3M19 9A8 8 0 0 0 4.8 6" />
    </>
  ),
  send: <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />,
  more: (
    <>
      <circle cx="5" cy="12" r="1.6" />
      <circle cx="12" cy="12" r="1.6" />
      <circle cx="19" cy="12" r="1.6" />
    </>
  ),
  'arrow-left': <path d="M20 12H4M11 5l-7 7 7 7" />,
  settings: (
    <>
      <circle cx="12" cy="12" r="3.2" />
      <path d="M19.4 13.5a7.7 7.7 0 0 0 0-3l2-1.4-2-3.4-2.3.8a7.7 7.7 0 0 0-2.6-1.5L14 2.5h-4l-.5 2.5a7.7 7.7 0 0 0-2.6 1.5l-2.3-.8-2 3.4 2 1.4a7.7 7.7 0 0 0 0 3l-2 1.4 2 3.4 2.3-.8a7.7 7.7 0 0 0 2.6 1.5l.5 2.5h4l.5-2.5a7.7 7.7 0 0 0 2.6-1.5l2.3.8 2-3.4-2-1.4z" />
    </>
  ),
  'type-text': <path d="M4 7h16M4 12h10M4 17h7" />,
  'type-longtext': <path d="M4 6h16M4 10h16M4 14h16M4 18h10" />,
  'type-number': <path d="M9 4L7 20M17 4l-2 16M4 9h16M3 15h16" />,
  'type-multiselect': (
    <>
      <rect x="3" y="4" width="5" height="5" rx="1" />
      <path d="M4.5 6.5l1 1 2-2" />
      <path d="M11 6.5h9" />
      <rect x="3" y="15" width="5" height="5" rx="1" />
      <path d="M4.5 17.5l1 1 2-2" />
      <path d="M11 17.5h9" />
    </>
  ),
  expand: (
    <>
      <path d="M9 4H4v5" />
      <path d="M15 20h5v-5" />
      <path d="M4 4l7 7" />
      <path d="M20 20l-7-7" />
    </>
  ),
  collapse: (
    <>
      <path d="M4 9h5V4" />
      <path d="M20 15h-5v5" />
      <path d="M11 11L4 4" />
      <path d="M13 13l7 7" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M2.5 19c0-3.3 2.9-5 6.5-5s6.5 1.7 6.5 5" />
      <path d="M16 4.3a3.2 3.2 0 0 1 0 6.2" />
      <path d="M17.5 14.3c2.8.5 4 1.9 4 4.7" />
    </>
  ),
};

export function Icon({ name, size = 18, className, strokeWidth = 1.6 }: { name: IconName; size?: number; className?: string; strokeWidth?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
