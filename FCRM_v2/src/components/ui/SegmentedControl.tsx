// Inline multi-value toggle for a queue's single most-scanned filter - the value is always
// visible instead of hidden behind a dropdown click. Same visual treatment everywhere it's used
// (analyst queue's "Assigned to me / All", its status filter, the committee queue's vote filter)
// so they read as the same kind of control.
export function SegmentedControl<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="flex rounded-md border border-border-strong overflow-hidden">
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`px-4 py-2 text-sm font-semibold cursor-pointer transition-colors ${
            value === opt.value ? 'bg-carbon-moss text-white' : 'bg-white text-text-secondary hover:bg-surface-tertiary'
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
