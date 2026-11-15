

export interface ResearchAskArticle {
  article_id?: string;
  slug?: string;
  title?: string;
  doi?: string | null;
  pmid?: string | null;
  pmcid?: string | null;
  journal?: string | null;
  study_type?: string | null;
  published_at?: string | null;
  discovery_score?: number | null;
  peer_review_status?: string | null;
  open_access_status?: string | null;
  open_access_url?: string | null;
  tags?: Array<string | { name?: string; label?: string }>;
  diseases?: Array<{ disease_id?: string; slug?: string; name_en?: string; name_zh?: string; name_fr?: string; confidence?: number }>;
  countries?: Array<{ code?: string; slug?: string; name_en?: string; name_zh?: string; name_fr?: string; confidence?: number }>;
  topics?: Array<{ name?: string; confidence?: number }>;
  summary?: Record<string, Record<string, unknown>>;
  why_it_matters_en?: string | null;
  why_it_matters_zh?: string | null;
  why_it_matters_fr?: string | null;
  related_signals?: ResearchAskSignal[];
}

export interface ResearchAskSignal {
  signal_id?: string;
  title?: string | null;
  disease_name_en?: string | null;
  disease_name_zh?: string | null;
  disease_name_fr?: string | null;
  geographies?: Array<{ code?: string; name?: string; name_en?: string; name_fr?: string }>;
  relation_level?: string;
  data_through?: string | null;
  situation_url?: string | null;
  visibility?: string | null;
}

export interface ResearchAskEvidence {
  article: ResearchAskArticle;
  score: number;
  matchedTerms: string[];
  evidenceLevel: 'exact' | 'background';
  matchReasons: ResearchAskMatchReason[];
  citation: ResearchAskCitation;
  findingEn: string | null;
  findingZh: string | null;
  findingFr: string | null;
  sourceReview?: { noticeEn: string; noticeZh: string; noticeFr: string; sourceUrl: string };
}

export interface ResearchAskMatchReason {
  kind: 'disease' | 'country' | 'topic' | 'study_type' | 'keyword';
  queryTerm: string;
  matchedAlias: string;
  fields: string[];
  points: number;
}

export interface ResearchAskCitation {
  index: number;
  marker: string;
  label: string;
  sourceUrl: string;
  doi: string | null;
  pmid: string | null;
}

export interface ResearchAskGap {
  kind: 'no_relevant_records' | 'no_exact_evidence' | 'missing_structured_summary' | 'no_linked_surveillance';
  messageEn: string;
  messageZh: string;
  messageFr?: string;
  count?: number;
}

export interface ResearchAskAnswer {
  query: string;
  normalizedQuery: string;
  evidence: ResearchAskEvidence[];
  exactEvidence: ResearchAskEvidence[];
  backgroundEvidence: ResearchAskEvidence[];
  citations: ResearchAskCitation[];
  gaps: ResearchAskGap[];
  signals: ResearchAskSignal[];
  themes: Array<{ name: string; count: number }>;
  summaryEn: string;
  summaryZh: string;
  summaryFr: string;
  limitationsEn: string;
  limitationsZh: string;
  limitationsFr: string;
}

export type ResearchFacetKind = 'disease' | 'country' | 'topic' | 'study_type' | 'keyword';

export interface AliasGroup {
  kind: Exclude<ResearchFacetKind, 'keyword'>;
  label: string;
  aliases: string[];
}

export interface SearchUnit {
  kind: ResearchFacetKind;
  label: string;
  aliases: string[];
}
