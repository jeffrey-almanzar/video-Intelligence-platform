// Phase 1 placeholder: proves the web app can reach the API. The real UI arrives in Phase 5.
export const dynamic = 'force-dynamic';

type Readiness = {
  status: 'ok' | 'degraded' | 'unavailable';
  checks: Record<string, { status: 'up' | 'down'; latencyMs: number; error?: string }>;
};

async function getReadiness(): Promise<Readiness | { error: string }> {
  const base = process.env.API_URL ?? `http://localhost:${process.env.API_PORT ?? 3000}`;
  try {
    const res = await fetch(`${base}/health/ready`, { cache: 'no-store' });
    return (await res.json()) as Readiness;
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'API unreachable' };
  }
}

export default async function Home() {
  const readiness = await getReadiness();
  return (
    <main>
      <h1>Video Intelligence Platform</h1>
      <p>Turn hours of video into actionable knowledge.</p>
      <h2>System status</h2>
      {'error' in readiness ? (
        <p>API unreachable: {readiness.error}</p>
      ) : (
        <>
          <p>
            Overall: <strong>{readiness.status}</strong>
          </p>
          <ul>
            {Object.entries(readiness.checks).map(([name, check]) => (
              <li key={name}>
                {check.status === 'up' ? '🟢' : '🔴'} {name} ({check.latencyMs} ms)
                {check.error ? `: ${check.error}` : ''}
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}
