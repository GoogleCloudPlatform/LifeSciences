# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ClinicalTrials.gov v2 REST API client + ADK FunctionTools (FREE).

Docs: https://clinicaltrials.gov/data-api/api

Note: ClinicalTrials.gov fingerprint-blocks httpx (any User-Agent fails with
403). We route this source through `_http.get_json_urllib` which uses the
stdlib urllib client running in a worker thread.
"""

from __future__ import annotations

from typing import Any

from . import _http
from ._http import UrllibStatusError

_BASE_URL = 'https://clinicaltrials.gov/api/v2'


def _err(e: Exception, ctx: str) -> dict[str, Any]:
  return {'error': True, 'message': str(e), 'context': ctx}


def _normalize_study(study: dict[str, Any]) -> dict[str, Any]:
  protocol = study.get('protocolSection', {}) or {}
  ident = protocol.get('identificationModule', {}) or {}
  status = protocol.get('statusModule', {}) or {}
  sponsor = protocol.get('sponsorCollaboratorsModule', {}) or {}
  design = protocol.get('designModule', {}) or {}
  cond = protocol.get('conditionsModule', {}) or {}
  arms = protocol.get('armsInterventionsModule', {}) or {}
  contacts = protocol.get('contactsLocationsModule', {}) or {}

  nct_id = ident.get('nctId')
  return {
      'nct_id': nct_id,
      'url': f'https://clinicaltrials.gov/study/{nct_id}' if nct_id else None,
      'title': ident.get('briefTitle'),
      'official_title': ident.get('officialTitle'),
      'status': status.get('overallStatus'),
      'phase': design.get('phases'),
      'study_type': design.get('studyType'),
      'enrollment': (design.get('enrollmentInfo') or {}).get('count'),
      'conditions': cond.get('conditions'),
      'interventions': [
          {
              'type': i.get('type'),
              'name': i.get('name'),
              'description': i.get('description'),
          }
          for i in (arms.get('interventions') or [])
      ],
      'sponsor': (sponsor.get('leadSponsor') or {}).get('name'),
      'collaborators': [c.get('name')
                        for c in (sponsor.get('collaborators') or [])],
      'start_date': (status.get('startDateStruct') or {}).get('date'),
      'completion_date': (
          status.get('primaryCompletionDateStruct') or {}
      ).get('date'),
      'locations_count': len(contacts.get('locations') or []),
  }


async def search_clinical_trials(
    query: str | None = None,
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    status: str | None = None,
    phase: str | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
  """Search ClinicalTrials.gov via the v2 REST API (FREE).

  Use for trial pipelines, competitive intelligence, eligibility scouting,
  or any time you want a structured view of the trial landscape for an
  indication, target, or sponsor.

  Args:
    query: Free-text essie query (any field), e.g. "HER2-low breast cancer".
    condition: Restrict to a condition (MeSH or free text), e.g.
      "Type 2 Diabetes".
    intervention: Restrict to an intervention (drug / device / procedure
      name).
    sponsor: Restrict to a sponsor name, e.g. "Pfizer".
    status: One of "RECRUITING", "ACTIVE_NOT_RECRUITING", "COMPLETED",
      "ENROLLING_BY_INVITATION", "TERMINATED", "WITHDRAWN", "SUSPENDED",
      "NOT_YET_RECRUITING", "UNKNOWN".
    phase: One of "EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4",
      "NA". Pass multiple as comma-separated, e.g. "PHASE2,PHASE3".
    max_results: 1-100 (default 20).

  Returns:
    `{returned_results, total_count, studies: [{nct_id, url, title, status,
    phase, study_type, conditions, interventions, sponsor, ...}, ...]}`.
  """
  params: dict[str, Any] = {
      'pageSize': str(min(max(max_results, 1), 100)),
      'format': 'json',
  }
  if query:
    params['query.term'] = query
  if condition:
    params['query.cond'] = condition
  if intervention:
    params['query.intr'] = intervention
  if sponsor:
    params['query.lead'] = sponsor
  if status:
    params['filter.overallStatus'] = status
  if phase:
    params['filter.advanced'] = (
        ' AND '.join(f'AREA[Phase]({p.strip()})' for p in phase.split(','))
    )

  if len(params) <= 2:
    return {'error': True,
            'message': 'At least one filter (query/condition/intervention/...) required'}

  try:
    data = await _http.get_json_urllib(f'{_BASE_URL}/studies', params=params)
    studies = data.get('studies', []) or []
    return {
        'returned_results': len(studies),
        'total_count': data.get('totalCount'),
        'studies': [_normalize_study(s) for s in studies],
    }
  except UrllibStatusError as e:
    return _err(e, f'search_clinical_trials: {query or condition}')


async def get_clinical_trial(nct_id: str) -> dict[str, Any]:
  """Fetch a single ClinicalTrials.gov study by NCT ID (FREE).

  Args:
    nct_id: Trial identifier, e.g. "NCT04267848".
  """
  try:
    data = await _http.get_json_urllib(
        f'{_BASE_URL}/studies/{nct_id}', params={'format': 'json'},
    )
    return {'study': _normalize_study(data)}
  except UrllibStatusError as e:
    if e.status == 404:
      return {'error': True, 'message': f'NCT {nct_id} not found'}
    return _err(e, f'get_clinical_trial: {nct_id}')
