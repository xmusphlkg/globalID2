#!/usr/bin/env python3
"""Audit or atomically rehearse/apply reviewed mappings for 31 unpublished areas.

No observation, publication gate or schema is modified. The default is a
read-only audit. --rehearse creates and activates a mapping release in one
transaction and rolls the entire transaction back.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import delete, select, text, update
from src.core import get_database
from src.domain import SourceDiseaseCategory, DiseaseMappingAssertion, DiseaseMappingRelease
from src.domain.disease_mapping_registry import DiseaseMappingCandidate, DiseaseMappingReleaseItem
from src.services.disease_mapping_config import DEFAULT_MAPPING_DIR, load_reviewed_source_category_mappings
from src.services.disease_mapping_registry_service import disease_mapping_registry_service

SUBJECTS = frozenset('AR BN BQ CL CO CR EC ET GF GH GP ID KE KH LA MM MQ MW MX MY NG PA PE PH PY TH TL TZ UG VN ZA'.split())
LOCK_KEY = 'globalid:disease_mapping_release:v1'
SEMANTIC_FIELDS = ('target_kind', 'target_code', 'mapping_relation', 'comparability', 'projection_policy', 'aggregation_policy')
BQ_SOURCE = 'SRC_NL_RIVM_NOTIFIABLE'


def semantics(item):
    return tuple(getattr(item, field) for field in SEMANTIC_FIELDS)


def select_manifests(mappings, subjects):
    unknown = set(subjects) - SUBJECTS
    if unknown:
        raise ValueError(f'Outside unpublished-country scope: {sorted(unknown)}')
    return [m for m in mappings if m.country_code in subjects or
            ('BQ' in subjects and m.country_code == 'NL' and m.source_id == BQ_SOURCE)]


def manifest_hashes(paths):
    return {str(Path(path).resolve()): hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in sorted(set(paths))}


def verify_manifest_hashes(snapshot):
    for path, expected in snapshot.items():
        try:
            actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError(f'Reviewed manifest changed or disappeared: {path}') from exc
        if actual != expected:
            raise RuntimeError(f'Reviewed manifest changed during review: {path}; transaction must roll back')


def load_frozen_manifests(subjects):
    # Hash before the loader reads CSV rows, then verify every selected file.
    # This prevents evidence from referring to a later on-disk revision.
    paths = [*DEFAULT_MAPPING_DIR.glob('*.csv'), *(DEFAULT_MAPPING_DIR / 'reviewed').glob('*.csv')]
    before = manifest_hashes(paths)
    mappings = select_manifests(load_reviewed_source_category_mappings(), subjects)
    selected_paths = {str((ROOT / mapping.source_path).resolve()) for mapping in mappings}
    missing = selected_paths - before.keys()
    if missing:
        raise RuntimeError(f'Reviewed manifest appeared while loading: {sorted(missing)}')
    snapshot = {path: before[path] for path in sorted(selected_paths)}
    verify_manifest_hashes(snapshot)
    return mappings, snapshot


def category_identity(category):
    return category.country_code, category.source_id, category.source_code, category.definition_version


def match_categories(mapping, indexed):
    # Opaque category codes are exact source identities, never case-folded.
    prefix = mapping.country_code, mapping.source_id, mapping.source_code
    if mapping.definition_version == '*':
        return [c for key, c in indexed.items() if key[:3] == prefix]
    category = indexed.get((*prefix, mapping.definition_version))
    return [category] if category is not None else []


def assertion_evidence(assertion):
    return {'id': assertion.id, 'assertion_key': assertion.assertion_key,
            'status': assertion.assertion_status,
            'semantics': dict(zip(SEMANTIC_FIELDS, semantics(assertion))),
            'reviewed_by': assertion.reviewed_by, 'review_notes': assertion.review_notes}


async def preflight(db):
    ready = (await db.execute(text('''SELECT
      to_regclass('public.source_disease_categories') IS NOT NULL
      AND to_regclass('public.disease_mapping_assertions_v3') IS NOT NULL
      AND to_regclass('public.disease_mapping_releases_v3') IS NOT NULL
      AND to_regclass('public.disease_mapping_release_items_v3') IS NOT NULL
      AND to_regclass('public.effective_disease_series_observations_v3') IS NOT NULL
      AND EXISTS (SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='source_disease_categories'
          AND column_name='ai_next_attempt_at')'''))).scalar_one()
    if not ready:
        raise RuntimeError('Registry v3 schema incomplete; deploy approved migrations first (this script never runs DDL).')
    disease_mapping_registry_service._schema_ready = True


async def prepare_review(db, mappings):
    countries = {m.country_code for m in mappings}
    sources = {m.source_id for m in mappings}
    categories = (await db.execute(select(SourceDiseaseCategory).where(
        SourceDiseaseCategory.country_code.in_(countries),
        SourceDiseaseCategory.source_id.in_(sources),
    ))).scalars().all()
    indexed = {category_identity(c): c for c in categories}
    plans, unmatched, claimed = [], [], {}
    for mapping in mappings:
        matches = match_categories(mapping, indexed)
        if not matches:
            unmatched.append(mapping.evidence())
        for category in matches:
            if category.id in claimed:
                raise ValueError(f'Overlapping manifest definitions for category {category.id}: {mapping.evidence()}')
            claimed[category.id] = mapping
            plans.append((category, mapping))
    assertions = defaultdict(list)
    ids = list(claimed)
    for start in range(0, len(ids), 2000):
        rows = (await db.execute(select(DiseaseMappingAssertion).where(
            DiseaseMappingAssertion.category_id.in_(ids[start:start + 2000])
        ))).scalars().all()
        for assertion in rows:
            assertions[assertion.category_id].append(assertion)
    conflicts = []
    for category, mapping in plans:
        active = [a for a in assertions[category.id] if a.assertion_status in {'approved', 'proposed', 'in_review'}]
        differing = [a for a in active if semantics(a) != semantics(mapping)]
        if differing:
            conflicts.append({'category_id': category.id,
                'country': category.country_code, **mapping.evidence(),
                'actual_definition_version': category.definition_version,
                'reviewed_semantics': dict(zip(SEMANTIC_FIELDS, semantics(mapping))),
                'existing_assertions': [assertion_evidence(a) for a in differing]})
    return plans, assertions, unmatched, conflicts


async def apply_review(db, plans, assertions, *, supersede, frozen_manifests=None):
    now = datetime.now(timezone.utc)
    chosen, changes = [], []
    snapshot = frozen_manifests if frozen_manifests is not None else manifest_hashes(
        [ROOT / mapping.source_path for _, mapping in plans]
    )
    for category, mapping in plans:
        active = [a for a in assertions[category.id] if a.assertion_status in {'approved', 'proposed', 'in_review'}]
        different = [a for a in active if semantics(a) != semantics(mapping)]
        if different and not supersede:
            raise ValueError(f'Conflicting reviewed mapping for category {category.id}')
        matching = [a for a in active if a.assertion_status == 'approved' and semantics(a) == semantics(mapping)]
        selected = min(matching, key=lambda a: a.id) if matching else None
        prior = [assertion_evidence(a) for a in active if a is not selected]
        source_path = str((ROOT / mapping.source_path).resolve())
        manifest_sha256 = snapshot[source_path]
        evidence = {**mapping.evidence(), 'actual_definition_version': category.definition_version,
            'manifest_sha256': manifest_sha256, 'reviewed_at': now.isoformat(),
            'reviewed_semantics': dict(zip(SEMANTIC_FIELDS, semantics(mapping)))}
        for old in active:
            if old is selected:
                continue
            old.assertion_status = 'superseded'
            old.metadata_ = {**(old.metadata_ or {}), 'superseded_by_local_review': evidence}
        if selected is None:
            selected = DiseaseMappingAssertion(
                assertion_key=f'LOCAL-REVIEW-{uuid4().hex}', category_id=category.id,
                **dict(zip(SEMANTIC_FIELDS, semantics(mapping))),
                assertion_status='approved', confidence_score=1.0,
                suggestion_method='reviewed_mapping_config', reviewed_by='unpublished_source_manifest_review',
                reviewed_at=now, review_notes=mapping.notes,
                reasoning='Exact source/category/version review from local manifest; observation facts unchanged.',
                evidence=[evidence], metadata_={'reviewed_mapping_config': True, 'superseded_assertions': prior},
                supersedes_assertion_id=prior[0]['id'] if prior else None,
            )
            db.add(selected)
        category.status = 'active'
        category.ai_status = 'not_required'
        category.ai_next_attempt_at = None
        await db.execute(update(DiseaseMappingCandidate).where(
            DiseaseMappingCandidate.category_id == category.id,
            DiseaseMappingCandidate.status == 'proposed',
        ).values(status='stale', updated_at=now))
        chosen.append(selected)
        if prior:
            changes.append({'category_id': category.id, 'prior': prior, 'review': evidence})
    await db.flush()
    return chosen, changes


def merged_assertion_ids(current, reviewed, category_ids):
    """Retain the active release outside precisely reviewed categories."""
    return {a.id for a in current if a.category_id not in category_ids} | {a.id for a in reviewed}


async def create_scoped_release(db, chosen, category_ids):
    # The lock is already held. Empty country scope makes the service retain
    # the entire active release; replace only exact reviewed categories below.
    release = await disease_mapping_registry_service.create_release(
        db, release_code=f'DMR-UNPUBLISHED-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}',
        created_by='unpublished_source_manifest_review', country_codes=set(),
        description='Reviewed mappings for unpublished areas; retain all unrelated active assertions.',
    )
    current = (await db.execute(select(DiseaseMappingAssertion).join(
        DiseaseMappingReleaseItem, DiseaseMappingReleaseItem.assertion_id == DiseaseMappingAssertion.id,
    ).where(DiseaseMappingReleaseItem.release_id == release.id))).scalars().all()
    expected_ids = merged_assertion_ids(current, chosen, category_ids)
    await db.execute(delete(DiseaseMappingReleaseItem).where(DiseaseMappingReleaseItem.release_id == release.id))
    for assertion_id in sorted(expected_ids):
        db.add(DiseaseMappingReleaseItem(release_id=release.id, assertion_id=assertion_id))
    await db.flush()
    assertions = sorted([a for a in current if a.category_id not in category_ids] + list(chosen), key=lambda a: a.assertion_key)
    payload = [{'key': a.assertion_key, 'category_id': a.category_id,
        'target': [a.target_kind, a.target_code], 'relation': a.mapping_relation,
        'valid': [a.valid_from.isoformat() if a.valid_from else None,
                  a.valid_to.isoformat() if a.valid_to else None]} for a in assertions]
    release.checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    release.metadata_ = {**(release.metadata_ or {}), 'assertion_count': len(assertions),
        'reviewed_category_count': len(category_ids), 'preserved_assertions': len(expected_ids - {a.id for a in chosen})}
    await disease_mapping_registry_service.activate_release(db, release.id)
    return release


async def run(*, mode='audit', countries=None, supersede_reviewed_conflicts=False):
    subjects = set(countries or SUBJECTS)
    mappings, frozen_manifests = load_frozen_manifests(subjects)
    async with get_database() as db:
        try:
            if mode == 'audit':
                await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            await db.execute(text("SET LOCAL lock_timeout = '15s'"))
            await preflight(db)
            if mode != 'audit':
                await db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': LOCK_KEY})
            active_id = (await db.execute(select(DiseaseMappingRelease.id).where(
                DiseaseMappingRelease.status == 'active'))).scalar_one_or_none()
            plans, assertions, unmatched, conflicts = await prepare_review(db, mappings)
            result = {'mode': mode, 'subjects': sorted(subjects), 'collection_countries': sorted({m.country_code for m in mappings}),
                'active_release_before': active_id, 'configured': len(mappings), 'matched_categories': len(plans),
                'manifest_sha256': frozen_manifests,
                'unmatched': unmatched, 'conflicts': conflicts, 'supersede_reviewed_conflicts': supersede_reviewed_conflicts,
                'ready': not conflicts or supersede_reviewed_conflicts}
            if mode != 'audit' and result['ready']:
                verify_manifest_hashes(frozen_manifests)
                chosen, changes = await apply_review(db, plans, assertions,
                    supersede=supersede_reviewed_conflicts, frozen_manifests=frozen_manifests)
                release = await create_scoped_release(db, chosen, {c.id for c, _ in plans})
                result['superseded'] = changes
                result['release'] = {'id': release.id, 'code': release.release_code, 'status': release.status,
                    'checksum': release.checksum, **release.metadata_}
            # Both apply and rehearsal must validate the loaded file versions
            # after all mapping/release work, before the transaction can end.
            verify_manifest_hashes(frozen_manifests)
            if mode == 'apply' and result['ready']:
                await db.commit()
                result['transaction'] = 'committed'
            else:
                await db.rollback()
                result['transaction'] = 'rolled_back'
            return result
        except BaseException:
            await db.rollback()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--rehearse', action='store_true')
    mode.add_argument('--apply', action='store_true')
    parser.add_argument('--country', action='append', choices=sorted(SUBJECTS), help='Repeat to select a subset; default all 31')
    parser.add_argument('--supersede-reviewed-conflicts', action='store_true', help='Explicitly supersede conflicting live assertions using the complete reviewed six-field semantics')
    parser.add_argument('--output', type=Path, help='Write the exact JSON report to this file')
    args = parser.parse_args()
    result = asyncio.run(run(mode='apply' if args.apply else 'rehearse' if args.rehearse else 'audit',
        countries=args.country, supersede_reviewed_conflicts=args.supersede_reviewed_conflicts))
    body = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body + '\n', encoding='utf-8')
    print(body)
    if not result['ready']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
