/** Stable public entry point for the deterministic research catalogue search. */
export type { ResearchAskArticle, ResearchAskSignal, ResearchAskEvidence, ResearchAskMatchReason, ResearchAskCitation, ResearchAskGap, ResearchAskAnswer } from './research-ask/types.ts';
export { researchQueryTerms } from './research-ask/query.ts';
export { rankResearchArticles } from './research-ask/ranking.ts';
export { answerResearchQuestion } from './research-ask/answer.ts';
