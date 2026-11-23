import type { VideoStatus } from '@/lib/types';
import styles from './StatusChip.module.css';

const LABELS: Record<VideoStatus, string> = {
  uploading: 'Uploading',
  uploaded: 'Queued',
  processing: 'Processing',
  ready: 'Ready',
  failed: 'Failed',
};

export function StatusChip({ status }: { status: VideoStatus }) {
  return <span className={`${styles.chip} ${styles[status]}`}>{LABELS[status]}</span>;
}
