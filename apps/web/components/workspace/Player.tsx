'use client';

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { usePlayer } from './PlayerContext';
import styles from './workspace.module.css';

type Playback = { url: string; contentType: string; source: 'proxy' | 'original' };

export function Player({ videoId, captions }: { videoId: string; captions: boolean }) {
  const { videoRef, setCurrentMs } = usePlayer();
  const [playback, setPlayback] = useState<Playback | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Playback>(`/v1/videos/${videoId}/playback`).then(setPlayback, (e: Error) =>
      setError(e.message),
    );
  }, [videoId]);

  if (error)
    return (
      <div className={styles.playerEmpty}>
        <p>The player isn’t available yet: {error}</p>
      </div>
    );
  if (!playback)
    return <div className={`skeleton ${styles.playerEmpty}`} aria-label="Loading player" />;
  return (
    <video
      ref={videoRef}
      className={styles.video}
      controls
      preload="metadata"
      onTimeUpdate={(e) => setCurrentMs(Math.round(e.currentTarget.currentTime * 1000))}
    >
      <source src={playback.url} type={playback.contentType} />
      {captions && (
        <track
          kind="captions"
          src={`/api/v1/videos/${videoId}/captions`}
          srcLang="en"
          label="Transcript"
          default
        />
      )}
      Your browser can’t play this video.
    </video>
  );
}
