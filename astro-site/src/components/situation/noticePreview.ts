import type { SituationAlertRecord } from '../../utils/situationTypes';

export function noticePreview(alerts: SituationAlertRecord[], diseaseId?: string) {
  const disease = diseaseId?.toUpperCase();
  return alerts
    .filter(alert => !disease || alert.disease_id?.toUpperCase() === disease)
    .sort((a, b) => b.published_date.localeCompare(a.published_date) || a.alert_id.localeCompare(b.alert_id))
    .slice(0, 8);
}
