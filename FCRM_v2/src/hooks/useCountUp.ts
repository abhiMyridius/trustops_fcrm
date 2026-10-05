import { useEffect, useState } from 'react';
import { animate } from 'framer-motion';

export function useCountUp(target: number, duration = 0.9): number {
  const [value, setValue] = useState(0);
  useEffect(() => {
    const controls = animate(0, target, {
      duration,
      ease: 'easeOut',
      // Round to one decimal, not a whole integer - a target like 1.4 must still read
      // as 1.4, not 1, once the animation settles.
      onUpdate: (v) => setValue(Math.round(v * 10) / 10),
    });
    return () => controls.stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);
  return value;
}
