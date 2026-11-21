import type { Metadata } from 'next';
import type { ReactNode } from 'react';

export const metadata: Metadata = {
  title: 'Video Intelligence Platform',
  description: 'Turn hours of video into actionable knowledge.',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{ fontFamily: 'system-ui, sans-serif', margin: 0, padding: '3rem', maxWidth: 720 }}
      >
        {children}
      </body>
    </html>
  );
}
