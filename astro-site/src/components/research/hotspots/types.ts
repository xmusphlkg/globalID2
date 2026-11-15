

export type HotspotPeriod = {
  period: string;
  label?: string;
  start_date?: string;
  article_count?: number;
  topic_mentions?: number;
};

export type StreamPoint = {
  period: string;
  count: number;
  share_of_articles?: number;
};

export type StreamSeries = {
  topic: string;
  slug: string;
  count_total: number;
  points: StreamPoint[];
};

export type HeatmapCell = {
  period: string;
  count: number;
  intensity?: number;
};

export type HeatmapRow = {
  key: string;
  disease_name_en: string;
  disease_name_zh?: string | null;
  disease_name_fr?: string | null;
  topic: string;
  count_total: number;
  cells: HeatmapCell[];
};

export type BurstArticle = {
  slug?: string | null;
  title?: string | null;
  journal?: string | null;
  published_at?: string | null;
};

export type Burst = {
  period: string;
  label?: string;
  topic: string;
  count: number;
  previous_count?: number;
  growth: number;
  share?: number;
  share_delta?: number;
  burst_score: number;
  articles?: BurstArticle[];
};

export type AlluvialPeriod = {
  period: string;
  label?: string;
  start_date?: string;
  end_date?: string;
};

export type AlluvialTopic = {
  topic: string;
  slug: string;
};

export type AlluvialNode = {
  period: string;
  topic: string;
  slug: string;
  count: number;
  rank?: number;
  y0?: number;
  y1?: number;
};

export type AlluvialLink = {
  topic: string;
  slug: string;
  source_period: string;
  target_period: string;
  source_count: number;
  target_count: number;
  value: number;
};

export type Hotspots = {
  streamgraph?: {
    periods?: HotspotPeriod[];
    series?: StreamSeries[];
  };
  heatmap?: {
    periods?: HotspotPeriod[];
    rows?: HeatmapRow[];
  };
  burst_timeline?: {
    bursts?: Burst[];
  };
  alluvial?: {
    periods?: AlluvialPeriod[];
    topics?: AlluvialTopic[];
    nodes?: AlluvialNode[];
    links?: AlluvialLink[];
  };
  interpretation_note?: {
    en?: string;
    zh?: string;
    fr?: string;
  };
};

export type ChartKey = 'stream' | 'heatmap' | 'burst' | 'migration';
