import type { CategoryScore } from '@/types/analyst';

const SHORT: Record<string, string> = {
  'Customer / entity': 'Customer',
  'Product and service': 'Product',
  'Geography': 'Geography',
  'Delivery channel and third party': 'Delivery',
};

/** Likelihood x Impact matrix - inherent as a hollow marker, residual as filled, so the movement controls produced is visible. */
export function RiskMatrix({ categories }: { categories: CategoryScore[] }) {
  const size = 280;
  const pad = 36;
  const cell = (size - pad) / 5;
  const pos = (level: number) => pad + (level - 0.5) * cell;

  // Residual maps back to an equivalent likelihood/impact point at the same inherent likelihood,
  // scaled down along the impact axis proportional to the retention factor, for a legible plot.
  function residualImpact(c: CategoryScore) {
    return Math.max(1, c.impact * (c.retention <= 0 ? 0.2 : Math.sqrt(c.retention)));
  }

  // Deconflict category labels that would otherwise land on top of each other (§8.4 mod, §10.5) -
  // a simple greedy nudge is enough for the 3-4 points this ever plots. Anchored above the
  // inherent marker specifically (never the residual one): inherent's impact is always >= the
  // residual's, so inherent is always the topmost of the pair - anchoring off residual instead
  // would put the label on top of the inherent marker whenever retention is high and the two
  // points sit close together.
  const labelPositions = categories.map((c) => ({
    category: c.category,
    x: pos(c.likelihood),
    y: size - (c.impact - 0.5) * cell - 14,
  }));
  for (let i = 1; i < labelPositions.length; i++) {
    for (let j = 0; j < i; j++) {
      if (Math.abs(labelPositions[i].x - labelPositions[j].x) < 42 && Math.abs(labelPositions[i].y - labelPositions[j].y) < 12) {
        labelPositions[i].y = labelPositions[j].y - 12;
      }
    }
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <svg width={size} height={size + 40} className="overflow-visible">
        {[1, 2, 3, 4, 5].map((l) => (
          <line key={`v${l}`} x1={pad + (l - 1) * cell} y1={pad} x2={pad + (l - 1) * cell} y2={size} stroke="#e5e8e6" />
        ))}
        {[1, 2, 3, 4, 5].map((i) => (
          <line key={`h${i}`} x1={pad} y1={size - (i - 1) * cell} x2={size} y2={size - (i - 1) * cell} stroke="#e5e8e6" />
        ))}
        <line x1={pad} y1={pad} x2={pad} y2={size} stroke="#9caaab" strokeWidth={1.5} />
        <line x1={pad} y1={size} x2={size} y2={size} stroke="#9caaab" strokeWidth={1.5} />
        {[1, 2, 3, 4, 5].map((l) => (
          <text key={`lx${l}`} x={pos(l)} y={size + 14} textAnchor="middle" fontSize="9" fill="#52787d">
            {l}
          </text>
        ))}
        {[1, 2, 3, 4, 5].map((i) => (
          <text key={`ly${i}`} x={pad - 10} y={size - (i - 0.5) * cell + 3} textAnchor="end" fontSize="9" fill="#52787d">
            {i}
          </text>
        ))}
        <text x={size / 2} y={size + 28} textAnchor="middle" fontSize="10" fontWeight="600" fill="#285055">
          Likelihood
        </text>
        <text x={pad - 26} y={pad - 10} textAnchor="middle" fontSize="10" fontWeight="600" fill="#285055">
          Impact
        </text>

        {categories.map((c, i) => {
          const x = pos(c.likelihood);
          const yInherent = size - (c.impact - 0.5) * cell;
          const yResidual = size - (residualImpact(c) - 0.5) * cell;
          return (
            <g key={c.category}>
              <line x1={x} y1={yInherent} x2={x} y2={yResidual} stroke="#c3d2d3" strokeDasharray="2,2" />
              <circle cx={x} cy={yInherent} r={7} fill="white" stroke="#194247" strokeWidth={2} />
              <circle cx={x} cy={yResidual} r={6} fill="#52E081" stroke="#163439" strokeWidth={1} />
              <text x={labelPositions[i].x} y={labelPositions[i].y} textAnchor="middle" fontSize="9" fontWeight="700" fill="#0C2226">
                {SHORT[c.category] ?? c.category}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="flex items-center gap-4 text-[11px] text-text-muted">
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full border-2 border-carbon-moss bg-white inline-block" /> Inherent
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-quantum-leaf inline-block" /> Residual
        </span>
      </div>
    </div>
  );
}
