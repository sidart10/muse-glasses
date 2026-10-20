import { useState } from 'react';
import { IconImage } from '@wearables-ui-toolkit/mrbd';
import aiStars from '@wearables-ui-toolkit/icons/svg/aistars3__outline.svg';

/**
 * Muse's official mascot, cut out onto black (black is see-through on the lens).
 * The clips are extracted from the user's own Muse app by tools/make_mascot.py and are
 * never committed; when they're missing the toolkit sparkle stands in.
 */
export type Mood = 'idle' | 'working' | 'making' | 'wave' | 'celebrate';

const ONE_SHOT: Mood[] = ['wave', 'celebrate'];

export function Mascot({ mood, size, onEnded }: { mood: Mood; size: number; onEnded?: () => void }) {
  const [missing, setMissing] = useState(false);
  if (missing) return <IconImage source={aiStars} style={{ width: size * 0.4, height: size * 0.4 }} />;
  return (
    <video
      key={mood}
      className="muse-mascot"
      src={`/mascot/${mood}.mp4`}
      poster={`/mascot/${mood}.jpg`}
      width={size}
      height={size}
      autoPlay
      muted
      playsInline
      loop={!ONE_SHOT.includes(mood)}
      onEnded={onEnded}
      onError={() => setMissing(true)}
      aria-hidden="true"
    />
  );
}
