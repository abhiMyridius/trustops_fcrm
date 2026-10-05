import { motion, useMotionValue, useTransform, animate } from 'framer-motion';
import { useEffect, useState } from 'react';

interface Props {
  pct: number;
  threshold: number;
  size?: number;
  strokeWidth?: number;
}

export function CompletionRing({ pct, threshold, size = 88, strokeWidth = 8 }: Props) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, pct));

  const progress = useMotionValue(0);
  const [display, setDisplay] = useState(0);
  const [justCrossed, setJustCrossed] = useState(false);
  const wasAbove = useState({ current: clamped >= threshold })[0];

  useEffect(() => {
    const controls = animate(progress, clamped, {
      duration: 0.7,
      ease: 'easeOut',
      onUpdate: (v) => setDisplay(Math.round(v)),
    });
    if (clamped >= threshold && !wasAbove.current) {
      wasAbove.current = true;
      setJustCrossed(true);
      const t = setTimeout(() => setJustCrossed(false), 900);
      return () => {
        controls.stop();
        clearTimeout(t);
      };
    }
    wasAbove.current = clamped >= threshold;
    return () => controls.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clamped]);

  const dashOffset = useTransform(progress, (v) => circumference - (v / 100) * circumference);
  // No -90 here: the svg itself carries -rotate-90 (so raw angle 0 = 3 o'clock lands at 12,
  // matching the fill's own convention) - subtracting again here fought that rotation instead
  // of matching it, which is what put the tick a quarter-turn off from the real threshold.
  const thresholdAngle = (threshold / 100) * 360;
  const thresholdRad = (thresholdAngle * Math.PI) / 180;
  const tickHalfLen = strokeWidth / 2 + 3;
  const tickOuter = radius + tickHalfLen;
  const tickInner = radius - tickHalfLen;
  const tickX1 = size / 2 + tickOuter * Math.cos(thresholdRad);
  const tickY1 = size / 2 + tickOuter * Math.sin(thresholdRad);
  const tickX2 = size / 2 + tickInner * Math.cos(thresholdRad);
  const tickY2 = size / 2 + tickInner * Math.sin(thresholdRad);

  const met = clamped >= threshold;

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--ink-4)" strokeWidth={strokeWidth} />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={met ? 'var(--color-quantum-leaf)' : 'var(--color-carbon-moss)'}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          style={{ strokeDashoffset: dashOffset }}
          animate={justCrossed ? { filter: ['drop-shadow(0 0 0px #52E081)', 'drop-shadow(0 0 6px #52E081)', 'drop-shadow(0 0 0px #52E081)'] } : {}}
          transition={{ duration: 0.9 }}
        />
        {/* threshold marker - a tick crossing the track, not a dot on it, so it doesn't read as
            a rendering defect when the fill has passed it */}
        <line x1={tickX1} y1={tickY1} x2={tickX2} y2={tickY2} stroke="var(--color-text-primary)" strokeWidth={2} strokeLinecap="round" />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-lg font-bold text-text-primary leading-none">{display}%</span>
        <span className="text-[9px] text-text-faint mt-0.5">{threshold}% to submit</span>
      </div>
    </div>
  );
}
