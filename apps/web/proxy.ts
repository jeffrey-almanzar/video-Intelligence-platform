import { type NextRequest, NextResponse } from 'next/server';

/**
 * Optimistic routing only (Next 16 "proxy", formerly middleware): without any session cookie,
 * app pages go straight to sign-in. Real authorization happens in NestJS on every API call.
 */
export function proxy(request: NextRequest) {
  const signedIn = request.cookies.has('vip_at') || request.cookies.has('vip_rt');
  const { pathname, search } = request.nextUrl;
  const authPage = pathname === '/login' || pathname === '/register';
  if (!signedIn && !authPage) {
    const url = new URL('/login', request.url);
    if (pathname !== '/') url.searchParams.set('next', pathname + search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // Pages only: not the API proxy, Next internals or static files.
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)'],
};
