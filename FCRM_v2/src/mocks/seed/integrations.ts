import type { Integration } from '@/types';

// Email is not in this list at all (§8.9 mod, §1) - it's the PO's own work address, not a
// third-party connection, and renders permanently in Settings instead. Every listed integration
// starts disconnected, reflecting that the PO uploads documents manually during intake by default.
export const INTEGRATIONS: Integration[] = [
  {
    id: 'sharepoint',
    name: 'SharePoint',
    category: 'Document sources',
    connected: false,
    enabled: false,
    description: 'Pull business cases, contracts, and diagrams straight from your team sites.',
  },
  {
    id: 'teams',
    name: 'Microsoft Teams',
    category: 'Communication apps',
    connected: false,
    enabled: false,
    description: 'Send notifications and digests to a Teams channel or DM.',
  },
  {
    id: 'slack',
    name: 'Slack',
    category: 'Communication apps',
    connected: false,
    enabled: false,
    description: 'Send notifications and digests to a Slack channel or DM.',
  },
  {
    id: 'mcp-datacatalog',
    name: 'Data Catalog MCP',
    category: 'MCP servers / apps',
    connected: false,
    enabled: false,
    description: 'Look up data classification and lineage for fields referencing internal systems.',
  },
];
