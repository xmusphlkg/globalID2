import type { APIRoute } from 'astro';
import sharp from 'sharp';
import { renderDefaultSocialCardSvg } from '../lib/site-social-card';

export const prerender = true;

export const GET: APIRoute = async () => {
  const png = await sharp(Buffer.from(renderDefaultSocialCardSvg()))
    .png({ compressionLevel: 9 })
    .toBuffer();
  return new Response(png, {
    headers: {
      'Content-Type': 'image/png',
      'Cache-Control': 'public, max-age=86400',
    },
  });
};
