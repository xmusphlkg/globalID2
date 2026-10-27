import type { APIRoute } from 'astro';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import sharp from 'sharp';
import researchRoutes from '../../../data/research/routes.json';
import { renderResearchSocialCardSvg } from '../../../lib/research-social-card';

export const prerender = true;

export function getStaticPaths() {
  const plan = process.env.GIDS_RESEARCH_BUILD_PLAN
    ? new Set(JSON.parse(readFileSync(process.env.GIDS_RESEARCH_BUILD_PLAN, 'utf8')).selected as string[])
    : null;
  return ((researchRoutes as any).articles ?? [])
    .filter((article: any) => article.indexable !== false && (!plan || plan.has(article.slug)))
    .map((article: any) => ({ params: { slug: article.slug }, props: { slug: article.slug } }));
}

export const GET: APIRoute = async ({ props }) => {
  const article: any = JSON.parse(readFileSync(resolve(`./src/data/research/articles/${props.slug}.json`), 'utf-8'));
  const svg = renderResearchSocialCardSvg({
    title: article.title,
    studyType: article.study_type,
    diseases: (article.diseases ?? []).map((item: any) => item.name_en),
    countries: (article.countries ?? []).map((item: any) => item.name_en),
    implication: article.why_it_matters_en,
    doi: article.doi,
  });
  const png = await sharp(Buffer.from(svg)).png({ compressionLevel: 9 }).toBuffer();
  return new Response(png, {
    headers: {
      'Content-Type': 'image/png',
      'Cache-Control': 'public, max-age=31536000, immutable',
    },
  });
};
