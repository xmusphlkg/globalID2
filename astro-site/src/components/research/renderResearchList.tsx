import { createRoot, type Root } from 'react-dom/client';
import ResearchArticleCard from './ResearchArticleCard';
import type { Lang } from '../../utils/i18n';

const roots = new WeakMap<HTMLElement, Root>();

export function renderResearchList(list: HTMLElement, articles: any[], locale: Lang) {
  let root = roots.get(list);
  if (!root) {
    root = createRoot(list);
    roots.set(list, root);
  }
  root.render(articles.map((article, index) => (
    <ResearchArticleCard key={article.article_id || article.doi || article.slug || article.pmid || index} article={article} index={index} locale={locale} />
  )));
}
