import type { Metadata } from 'next';
import { Workspace } from '@/components/workspace/Workspace';

export const metadata: Metadata = { title: 'Video' };

export default async function VideoPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Workspace videoId={id} />;
}
