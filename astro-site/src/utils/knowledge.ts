export type KnowledgeLanguage = 'en' | 'zh';

export type KnowledgeTextAssessment = {
  available: boolean;
  status: 'available' | 'missing' | 'insufficient_evidence' | 'language_mismatch' | 'not_applicable';
  reason: string | null;
};

const citationPattern = /\[(?:\d+(?:\s*[-,]\s*\d+)*)\]/g;
const cjkPattern = /[\u3400-\u9fff]/g;
const latinPattern = /[A-Za-z]/g;
const unavailableEnPattern = /(?:not\s+(?:yet\s+)?available|no\s+source[- ]backed\s+(?:detail|description|information)|(?:sources?|snippets?|materials?|payload|evidence|records?|excerpts?|metadata)\b.{0,80}?(?:do\s+not|does\s+not|did\s+not|cannot|can't|lack)\s+(?:provide|describe|identify|include|support|state|supply|contain|define|report|specify|establish)|cannot\s+be\s+(?:stated|confirmed|inferred|determined)|no\s+.{0,180}(?:can\s+be\s+(?:stated|confirmed|inferred)|(?:is|are)\s+(?:available|provided|described|stated|identified|reported|specified))|(?:is|are)\s+not\s+(?:available|provided|described|stated|identified|reported|specified)|considered\s+unavailable|would\s+need\s+to\s+come\s+from|insufficient\s+(?:evidence|detail|information)|would\s+be\s+speculative|does\s+not\s+infer|should\s+remain\s+review[- ]gated|read\s+as\s+a\s+placeholder)/i;
const unavailableZhPattern = /(?:(?:尚未|未能|无法|不能|不足以|不宜|尚无|暂无|未检出|未明确|未系统).{0,28}(?:提供|形成|支持|确认|描述|推断|列出|摘要|信息|细节|结论)|(?:来源|材料|片段|证据|资料|信息).{0,20}(?:不足|缺失|有限|未提供|未描述|未给出|未明确|无法)|不作推断|不能据此|未作说明|未包含|尚未可得|尚不可用|(?:源)?依据不足|尚?缺乏.{0,16}(?:支持|依据|信息|细节|证据)|尚未\s*(?:可得|可用|获得|提供|具备|[\u0900-\u097f]{2,})|尚未.{0,16}(?:获得|得到).{0,8}支持|(?:没有|无).{0,20}(?:资料|证据|信息).{0,20}(?:说明|支持|提供|描述|表明)|(?:来源|材料|片段|证据|资料|信息).{0,40}(?:没有给出|没有提供|没有描述)|不能仅凭.{0,30}(?:判断|推断|确认)|不能从.{0,30}(?:导出|提炼|得出)|不应(?:据此)?外推|适合用于.{0,20}(?:检索|证据汇编)|需进一步补充.{0,30}(?:条目|资料|证据)|不是已具备.{0,30}(?:疾病概述|画像|档案)|需(?:要|依赖).{0,16}(?:补充|支持|核验|来源)|尚未充分可得|(?:源文)?(?:细节|信息).{0,10}(?:尚)?不足|仅(?:能)?表明.{0,50}(?:未提供|不足)|并未.{0,30}(?:给出|提供|描述)|建议标注|未证实)/;
const metadataOnlyEnPattern = /(?:article|paper|publication|review)\s+title|(?:scholarly|bibliographic|publication)\s+(?:metadata|attention|record)|available\s+metadata\s+includes?|(?:available\s+)?records?\s+are\s+scholarly\s+citations|(?:records?|sources?)\s+(?:mainly\s+)?(?:point|refer)\s+to\s+(?:review\s+)?literature|topic\s+(?:has\s+generated|was\s+addressed|is\s+a\s+recognized\s+topic)/i;
const metadataOnlyZhPattern = /(?:论文|文章|文献)(?:题名|题录|标题|条目)|(?:来源)?(?:题名|题录|标题)|(?:学术|专业|既往|持续)(?:研究)?(?:关注|焦点)|(?:研究方向|研究脉络|综述性文献|专题文献|专题讨论|研究相关|研究语境)/;

export function normalizeKnowledgeText(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const text = value.replace(/\s+/g, ' ').trim();
  if (!text || ['none', 'null', 'n/a', 'na', 'unknown', '-', '—'].includes(text.toLowerCase())) {
    return null;
  }
  return text;
}

export function assessKnowledgeText(value: unknown, language: KnowledgeLanguage): KnowledgeTextAssessment {
  const text = normalizeKnowledgeText(value);
  if (!text) return { available: false, status: 'missing', reason: 'empty' };

  const plainText = text.replace(citationPattern, '');
  const cjkCount = plainText.match(cjkPattern)?.length ?? 0;
  const latinCount = plainText.match(latinPattern)?.length ?? 0;
  const minimumCharacterCount = language === 'zh' ? 8 : 12;
  if (cjkCount + latinCount < minimumCharacterCount) {
    return { available: false, status: 'insufficient_evidence', reason: 'too_short' };
  }
  if (language === 'zh' && cjkCount < 8 && latinCount >= 24) {
    return { available: false, status: 'language_mismatch', reason: 'expected_zh' };
  }
  if (language === 'en' && cjkCount >= 12 && cjkCount > latinCount) {
    return { available: false, status: 'language_mismatch', reason: 'expected_en' };
  }

  const sentences = plainText.split(/(?<=[.!?。！？；;])\s*|\n+/).map((item) => item.trim()).filter(Boolean);
  const pattern = language === 'zh' ? unavailableZhPattern : unavailableEnPattern;
  const metadataPattern = language === 'zh' ? metadataOnlyZhPattern : metadataOnlyEnPattern;
  const unavailableCount = sentences.filter((sentence) => pattern.test(sentence) || metadataPattern.test(sentence)).length;
  if (unavailableCount === sentences.length) {
    return {
      available: false,
      status: 'insufficient_evidence',
      reason: 'evidence_unavailable_placeholder',
    };
  }
  return { available: true, status: 'available', reason: null };
}

export function resolveKnowledgeText(
  payloads: unknown[],
  field: string,
  language: KnowledgeLanguage,
  aliases: string[] = [],
): string | null {
  for (const payload of payloads) {
    if (!payload || typeof payload !== 'object') continue;
    const record = payload as Record<string, unknown>;
    const explicitStatuses = record.knowledge_field_status;
    for (const candidate of [field, ...aliases]) {
      if (explicitStatuses && typeof explicitStatuses === 'object') {
        const fieldStatuses = (explicitStatuses as Record<string, unknown>)[candidate];
        if (fieldStatuses && typeof fieldStatuses === 'object') {
          const explicitStatus = (fieldStatuses as Record<string, unknown>)[language];
          if (typeof explicitStatus === 'string' && explicitStatus !== 'available') continue;
        }
      }
      const value = stripUnavailableKnowledgeSentences(record[`${candidate}_${language}`], language);
      if (value && assessKnowledgeText(value, language).available) return value;
    }
  }
  return null;
}

export function stripUnavailableKnowledgeSentences(value: unknown, language: KnowledgeLanguage): string | null {
  const text = normalizeKnowledgeText(value);
  if (!text) return null;
  const sentences = text.split(/(?<=[.!?。！？；;])\s*|\n+/).map((item) => item.trim()).filter(Boolean);
  if (sentences.length === 0) return text;
  const unavailable = sentences.map((sentence) => {
    const plain = sentence.replace(citationPattern, '');
    const unavailablePattern = language === 'zh' ? unavailableZhPattern : unavailableEnPattern;
    const metadataPattern = language === 'zh' ? metadataOnlyZhPattern : metadataOnlyEnPattern;
    return unavailablePattern.test(plain) || metadataPattern.test(plain);
  });
  const unavailableCount = unavailable.filter(Boolean).length;
  if (unavailableCount === sentences.length) return null;
  if (unavailableCount * 2 >= sentences.length) {
    return sentences.filter((_, index) => !unavailable[index]).join(' ');
  }
  return text;
}
