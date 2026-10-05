// Scripted screening (Path A) conversation - build prompt §6.2.
// Ends in auto-clear: a copy change to an internal-only admin screen.
// No file upload required; free text at any step advances the script.

export interface ScreeningExchange {
  id: string;
  aiText: string;
  replyChips: string[];
}

export const SCREENING_EXCHANGES: ScreeningExchange[] = [
  {
    id: 's1',
    aiText: "Tell me about the change - what's happening, and does it touch customers, money movement, or third parties in any new way?",
    replyChips: ['It’s an internal UI/copy change', 'New vendor or partner', 'New product or feature', 'Not sure'],
  },
  {
    id: 's2',
    aiText: 'Got it - an internal UI change. Does it change what data is collected or shown, who can access it, or any customer-facing wording?',
    replyChips: ['No data or access change', 'Just wording', 'Not sure'],
  },
  {
    id: 's3',
    aiText: 'One more check - is this screen used by customers, or only by internal staff?',
    replyChips: ['Internal staff only', 'Customer-facing'],
  },
];

export const AUTO_CLEAR_SUMMARY =
  "This is a wording change on an internal-only admin screen (case-notes label text) - no customer-facing surface, no new data collection, no access changes, and no third party involved. That combination doesn't meet any FCRM trigger category, so I'm clearing this as a light-touch record rather than opening a full case.";
