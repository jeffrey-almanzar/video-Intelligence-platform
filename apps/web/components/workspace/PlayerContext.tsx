'use client';

import {
  createContext,
  type ReactNode,
  type RefObject,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from 'react';

export type Range = { startMs: number; endMs: number };

type PlayerState = {
  videoRef: RefObject<HTMLVideoElement | null>;
  currentMs: number;
  setCurrentMs: (ms: number) => void;
  durationMs: number;
  /** Jumps the player to `ms` and highlights `focus` on the Index Strip. */
  seek: (ms: number, focus?: Range[]) => void;
  focus: Range[];
  setFocus: (ranges: Range[]) => void;
};

const Ctx = createContext<PlayerState | null>(null);

export function PlayerProvider({
  durationMs,
  children,
}: {
  durationMs: number;
  children: ReactNode;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [currentMs, setCurrentMs] = useState(0);
  const [focus, setFocus] = useState<Range[]>([]);

  const seek = useCallback((ms: number, ranges?: Range[]) => {
    const v = videoRef.current;
    if (v) {
      v.currentTime = ms / 1000;
      void v.play().catch(() => undefined); // autoplay may be blocked; the seek still happens
    }
    setCurrentMs(ms);
    setFocus(ranges ?? [{ startMs: ms, endMs: ms }]);
  }, []);

  const value = useMemo(
    () => ({ videoRef, currentMs, setCurrentMs, durationMs, seek, focus, setFocus }),
    [currentMs, durationMs, seek, focus],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePlayer(): PlayerState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('usePlayer outside PlayerProvider');
  return ctx;
}
