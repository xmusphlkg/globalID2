import type { APIRoute } from 'astro';
import sharp from 'sharp';
import researchData from '../../../data/research/index.json';
import { renderResearchSocialCardSvg } from '../../../lib/research-social-card';

export const prerender = true;

export function getStaticPaths() {
  const data: any = researchData;
  return [...(data.articles ?? []), ...(data.preprints ?? [])]
    .filter((article: any) => article.indexable !== false)
    .map((article: any) => ({ params: { slug: article.slug }, props: { article } }));
}

export const GET: APIRoute = async ({ props }) => {
  const article: any = props.article;
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
