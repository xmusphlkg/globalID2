import settings from '../../../shared/research-publishing.json' with { type: 'json' };
import { MAIN_ORIGIN } from './papers.ts';

export const researchSocialCardsEnabled = settings.article_social_cards_enabled === true;

export function researchSocialImage(slug: string): string {
  return researchSocialCardsEnabled
    ? `/research/cards/${slug}.png`
    : `${MAIN_ORIGIN}/og-default.png`;
}
