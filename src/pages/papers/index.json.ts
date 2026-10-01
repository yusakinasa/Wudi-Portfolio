import type { APIRoute } from 'astro';
import { paperIndex } from '../../lib/papers';
export const prerender = true;
export const GET: APIRoute = () => new Response(JSON.stringify(paperIndex()), {
  headers: { 'Content-Type': 'application/json; charset=utf-8' }
});
