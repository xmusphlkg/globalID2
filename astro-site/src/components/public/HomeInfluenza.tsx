import { useSurveillanceSummary } from './useSurveillanceSummary';
import { pick, type Language } from '../../lib/surveillance-presentation.ts';
import './home-influenza.css';
import { curveSegments } from '../../lib/home-influenza-model';

export default function HomeInfluenza({ language = 'en' }: { language?: Language }) {
  const { data, version } = useSurveillanceSummary('/site-data/home-influenza.json');
  if (!data) return null;
  const curves = ['JP', 'CN'].map((code, index) => ({ code, index, row: data.series?.[code], segments: curveSegments(data.series?.[code]?.cases ?? [], index ? 738 : 724, index ? 224 : 246, data.series?.[code]?.dates) }));
  if (curves.some(curve => !curve.segments.length)) return null;
  return <div className="curve-field" aria-hidden="true" data-surveillance-version={version}>
    <svg viewBox="0 0 1440 760" preserveAspectRatio="none"><g className="curve-lines">{curves.flatMap(curve => curve.segments.map((points, index) => <polyline key={`${curve.code}:${index}`} data-country={curve.code} className={`curve-line curve-line-${curve.index ? 'b' : 'a'}`} pathLength="1" points={points} />))}</g></svg>
    <p className="curve-annotation"><b>{pick(language, 'Japan influenza · China influenza', '日本流感 · 中国流感', 'Grippe au Japon · grippe en Chine')}</b><br />{pick(language, 'Each series normalized independently', '各序列独立归一化', 'Chaque série est normalisée séparément')}</p>
  </div>;
}
