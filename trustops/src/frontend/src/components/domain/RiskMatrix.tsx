/**
 * 5 × 5 likelihood × impact grid. Inherent position is a hollow ring, residual a filled dot (design C
 * §1.6). Positions are the server's numbers. Cells are shaded by grid position only (darker toward
 * high L and I); the grid never maps a cell to a band, which is the scoring engine's job.
 */
import type { RiskArea } from '@/api/types/analyst';

const CELL = 44;
const PAD = 34;


export function RiskMatrix({ areas }: { areas: RiskArea[] }) {
  const size = PAD + CELL * 5 + 8;
  const summary = areas
    .map((a) => `${a.label}: likelihood ${a.L}, impact ${a.I}, inherent ${a.inherent}, residual ${a.residual}`)
    .join('; ');
  return (
    <figure className="flex flex-col gap-2">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Risk matrix. ${summary}`}>
        {Array.from({ length: 5 }, (_r, row) =>
          Array.from({ length: 5 }, (_c, col) => {
            const L = col + 1;
            const I = 5 - row;
            return (
              <rect
                key={`${row}-${col}`}
                x={PAD + col * CELL}
                y={row * CELL}
                width={CELL - 2}
                height={CELL - 2}
                rx={4}
                fill="var(--color-digital-pavo)"
                fillOpacity={0.05 + ((L + I - 2) / 8) * 0.3}
                stroke="var(--color-border)"
              />
            );
          }),
        )}
        {[1, 2, 3, 4, 5].map((n) => (
          <text key={`x${n}`} x={PAD + (n - 1) * CELL + CELL / 2 - 1} y={CELL * 5 + 14} fontSize="11" textAnchor="middle" fill="var(--color-text-muted)">
            {n}
          </text>
        ))}
        {[1, 2, 3, 4, 5].map((n) => (
          <text key={`y${n}`} x={PAD - 10} y={(5 - n) * CELL + CELL / 2 + 3} fontSize="11" textAnchor="middle" fill="var(--color-text-muted)">
            {n}
          </text>
        ))}
        <text x={PAD + (CELL * 5) / 2} y={CELL * 5 + 28} fontSize="11" textAnchor="middle" fill="var(--color-text-secondary)">
          Likelihood
        </text>
        <text x={10} y={(CELL * 5) / 2} fontSize="11" textAnchor="middle" fill="var(--color-text-secondary)" transform={`rotate(-90 10 ${(CELL * 5) / 2})`}>
          Impact
        </text>
        {areas.map((a, i) => {
          const cx = PAD + (a.L - 1) * CELL + CELL / 2 - 1 + (i - 1) * 9;
          const cy = (5 - a.I) * CELL + CELL / 2 - 1;
          return (
            <g key={a.domain}>
              <circle cx={cx} cy={cy} r={8} fill="none" stroke="var(--color-quantum-leaf)" strokeWidth={2} />
              <circle cx={cx} cy={cy} r={3.5} fill="var(--color-quantum-leaf)" />
            </g>
          );
        })}
      </svg>
      <figcaption className="flex flex-col gap-0.5 text-[12px] text-text-secondary">
        <span>Ring = inherent position · dot = area plotted at its L × I; residual after controls is listed below.</span>
        {areas.map((a) => (
          <span key={a.domain}>
            {a.label}: inherent {a.inherent} → residual {a.residual}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
