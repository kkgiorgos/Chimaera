import { NextRequest } from 'next/server';

export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';
const controller = process.env.CONTROLLER_URL || 'http://127.0.0.1:8080';

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!path.every(part => /^[a-zA-Z0-9_-]+$/.test(part))) return Response.json({ error: 'Invalid path' }, { status: 400 });
  if (request.method !== 'GET') {
    const origin = request.headers.get('origin');
    if (origin && origin !== request.nextUrl.origin && new URL(origin).host !== request.headers.get('host')) {
      return Response.json({ error: 'Cross-origin requests are not allowed' }, { status: 403 });
    }
    if (!request.headers.get('content-type')?.startsWith('application/json')) return Response.json({ error: 'JSON required' }, { status: 415 });
  }
  try {
    const response = await fetch(`${controller}/${path.join('/')}`, {
      method: request.method,
      headers: { 'Content-Type': 'application/json' },
      body: request.method === 'GET' ? undefined : await request.text(),
      cache: 'no-store',
      signal: request.signal,
    });
    const headers = new Headers({ 'Cache-Control': 'no-store' });
    for (const key of ['content-type', 'content-disposition', 'content-length']) {
      const value = response.headers.get(key); if (value) headers.set(key, value);
    }
    return new Response(response.body, { status: response.status, headers });
  } catch {
    return Response.json({ error: 'The scheduler is unavailable. Running cases continue on their machines.' }, { status: 503 });
  }
}
export { proxy as GET, proxy as POST };
