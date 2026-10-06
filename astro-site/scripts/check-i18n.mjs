import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  FR_UI,
  LOCALES,
  RESEARCH_FACET_LABELS,
  RESEARCH_TOPIC_LABELS,
  STRINGS,
  STUDY_TYPE_LABELS,
  SUPPORTED_LANGS,
} from '../src/utils/i18n.ts';
import { changelogReleases } from '../src/data/changelog.ts';

const failures = [];
const fail = (message) => failures.push(message);

const localeCodes = LOCALES.map((locale) => locale.code);
if (new Set(localeCodes).size !== localeCodes.length) fail('locale registry contains duplicate codes');
if (new Set(LOCALES.map((locale) => locale.pathPrefix)).size !== LOCALES.length) fail('locale registry contains duplicate path prefixes');
for (const language of SUPPORTED_LANGS) {
  if (!localeCodes.includes(language)) fail(`locale registry is missing ${language}`);
}

for (const [key, resource] of Object.entries(STRINGS)) {
  for (const language of SUPPORTED_LANGS) {
    if (typeof resource[language] !== 'string' || !resource[language].trim()) {
      fail(`STRINGS.${key}.${language} is missing`);
    }
  }
}
for (const [catalogue, entries] of Object.entries({ STUDY_TYPE_LABELS, RESEARCH_TOPIC_LABELS, RESEARCH_FACET_LABELS })) {
  for (const [key, resource] of Object.entries(entries)) {
    for (const language of SUPPORTED_LANGS) {
      if (typeof resource[language] !== 'string' || !resource[language].trim()) {
        fail(`${catalogue}.${key}.${language} is missing`);
      }
    }
  }
}
for (const [source, translation] of Object.entries(FR_UI)) {
  if (!source.trim() || !translation.trim()) fail(`empty French UI entry for ${JSON.stringify(source)}`);
}
for (const release of changelogReleases) {
  if (!release.titleFr || !release.summaryFr) fail(`changelog ${release.version} is missing French title or summary`);
  for (const section of release.sections) {
    if (!section.labelFr) fail(`changelog ${release.version}/${section.labelEn} is missing its French label`);
    section.items.forEach((item, index) => {
      if (!item.fr) fail(`changelog ${release.version}/${section.labelEn}/${index + 1} is missing French copy`);
    });
  }
}

const sourceChecks = [
  {
    path: 'src/pages/terms.astro',
    required: ['termsFrParagraphs', 'French terms coverage mismatch', 'paragraph.fr'],
    forbidden: ['translateUi(en)', 'pick(paragraph.en, paragraph.zh)</p>'],
  },
  {
    path: 'src/components/public/PublicCopyright.astro',
    required: ['Missing French copyright translation', "const fr = locale === 'fr'", "'18 août 2026'"],
    forbidden: [],
  },
  {
    path: 'src/components/Footer.astro',
    required: ['createTranslator(initialLanguage)', 'Responsable du projet', 'Droits d’auteur'],
    forbidden: [": fr ? 'Sources and methods'", ": fr ? 'Copyright notice'"],
  },
  {
    path: 'src/components/charts/ReportV4Panel.tsx',
    required: ["Traduction française en attente.", "fr: 'Évaluation actuelle'", "'Cas (mensuel)'"],
    forbidden: ['return typeof value === \'string\' ? value : fallback;', 'const zh = record.zh;'],
  },
  {
    path: 'src/components/charts/DiseaseDetailView.tsx',
    required: ["Traduction française en attente.", "fr: 'Points clés'", "'Cas cumulés'"],
    forbidden: ['value?.en || value?.zh'],
  },
  {
    path: 'src/pages/countries/[country]/reports/[id].astro',
    required: ['Titre français du rapport', 'Résumé français en attente.'],
    forbidden: ['const reportSummaryFr = document?.summary?.fr ?? reportSummaryEn'],
  },
  {
    path: 'src/components/public/PublicHome.astro',
    required: ["if (locale === 'fr') Object.assign(copy", 'localizedDiseaseName(disease, \'fr\')'],
    forbidden: [],
  },
];
for (const check of sourceChecks) {
  const content = readFileSync(resolve(check.path), 'utf8');
  for (const marker of check.required) {
    if (!content.includes(marker)) fail(`${check.path} is missing i18n marker: ${marker}`);
  }
  for (const marker of check.forbidden) {
    if (content.includes(marker)) fail(`${check.path} contains a silent fallback: ${marker}`);
  }
}

const missingGeneratedInputs = [];
const readJsonIfPresent = (relativePath) => {
  const path = resolve(relativePath);
  if (!existsSync(path)) {
    missingGeneratedInputs.push(relativePath);
    return null;
  }
  return JSON.parse(readFileSync(path, 'utf8'));
};

// Production releases provide database-backed snapshots, while clean CI
// checkouts deliberately create only a minimal deterministic fixture. Keep
// strict French-content checks for snapshots that are present without making
// the fixture build depend on ignored production data.
const report50 = readJsonIfPresent('src/data/reports/50.json');
if (report50) {
  for (const [label, document] of Object.entries({
    metadata: report50.metadata?.report_document_v4,
    top: report50.report_document_v4,
  })) {
    if (!document?.locales?.includes('fr')) fail(`reports/50 ${label} document does not declare fr locale`);
    if (!document?.title?.fr || !document?.summary?.fr || !document?.key_findings?.fr?.length) fail(`reports/50 ${label} document is missing French summary content`);
    for (const section of document?.sections ?? []) {
      if (!section.title?.fr || !section.body?.fr) fail(`reports/50 ${label}/${section.id} is missing French content`);
    }
  }
}
for (const situationPath of ['src/data/situation/v3/latest.json', 'src/data/situation/v3/weekly/2026-W34.json']) {
  const situation = readJsonIfPresent(situationPath);
  if (!situation) continue;
  const isDeterministicFixture = situation.method?.code_version === 'fixture'
    || situation.quality_gate?.checks?.some((check) => check?.id === 'fixture');
  if (isDeterministicFixture) continue;
  for (const key of ['narrative', 'limitations']) {
    if (!situation[key]?.fr) fail(`${situationPath} is missing ${key}.fr`);
  }
  if (!situation.coverage?.note?.fr) fail(`${situationPath} is missing coverage.note.fr`);
}

const visibleText = (html) => html
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
  .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ')
  .replace(/<[^>]+>/g, ' ')
  .replaceAll('&amp;', '&')
  .replaceAll('&#39;', "'")
  .replaceAll('&quot;', '"')
  .replace(/\s+/g, ' ')
  .trim();

const walkHtml = (directory) => {
  if (!existsSync(directory)) return [];
  const files = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...walkHtml(path));
    else if (entry.isFile() && entry.name === 'index.html') files.push(path);
  }
  return files;
};

if (process.argv.includes('--dist')) {
  const builtChecks = [
    {
      path: 'dist/fr/copyright/index.html',
      required: ['Droits d’auteur, licences et réutilisation', 'Comment citer GIDS', 'Cette page fournit des indications générales de réutilisation'],
      forbidden: ['Copyright, licensing & reuse', 'Using GIDS at a glance', 'Rights in GIDS-created material are reserved'],
    },
    {
      path: 'dist/fr/terms/index.html',
      required: ['Conditions du service et avis de confidentialité GIDS', 'Acceptation et champ d’application', 'GIDS ne vend pas les adresses e-mail'],
      forbidden: ['Acceptance and Scope', 'These Service Terms and Privacy Notice govern', 'Last updated:'],
    },
    {
      path: 'dist/fr/index.html',
      required: ['Responsable du projet', 'Sources et méthodes', 'Avis sur les droits d’auteur et les licences'],
      forbidden: ['Sources and methods', 'Copyright and licensing notice', 'Maintainer'],
    },
  ];
  for (const check of builtChecks) {
    const fullPath = resolve(check.path);
    if (!existsSync(fullPath)) {
      fail(`${check.path} does not exist; build the site before the dist check`);
      continue;
    }
    const text = visibleText(readFileSync(fullPath, 'utf8'));
    for (const marker of check.required) {
      if (!text.includes(marker)) fail(`${check.path} is missing French text: ${marker}`);
    }
    for (const marker of check.forbidden) {
      if (text.includes(marker)) fail(`${check.path} contains English fallback text: ${marker}`);
    }
  }

  const pendingMarkers = [
    'Traduction française en attente',
    'Translation pending.',
  ];
  for (const fullPath of walkHtml(resolve('dist/fr'))) {
    const text = visibleText(readFileSync(fullPath, 'utf8'));
    for (const marker of pendingMarkers) {
      if (text.includes(marker)) {
        fail(`${fullPath.replace(`${resolve('dist')}/`, '')} contains visible French translation placeholder: ${marker}`);
      }
    }
  }
}

if (failures.length) {
  console.error(`[i18n] FAIL (${failures.length})`);
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

const generatedNote = missingGeneratedInputs.length
  ? ` generated=skipped(${missingGeneratedInputs.length} missing ignored snapshot${missingGeneratedInputs.length === 1 ? '' : 's'})`
  : '';
console.log(`[i18n] PASS locales=${SUPPORTED_LANGS.join(',')} strings=${Object.keys(STRINGS).length} frenchUi=${Object.keys(FR_UI).length}${process.argv.includes('--dist') ? ' dist=checked' : ''}${generatedNote}`);
