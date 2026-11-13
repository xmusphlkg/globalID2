import { modelsPublishMessages } from "@/features/control-plane/models-publish-messages";

const keys = {
  static: "cc_mp_kind_static",
  surveillance_json: "cc_mp_kind_surveillance_json",
  literature_json: "cc_mp_kind_literature_json",
  situation_json: "cc_mp_kind_situation_json",
  unknown_json: "cc_mp_kind_unknown_json",
  reports: "cc_mp_kind_reports",
  code: "cc_mp_kind_code",
  theme: "cc_mp_kind_theme",
  raw_archive: "cc_mp_kind_raw_archive",
} as const;

export type PublicationKind = keyof typeof keys;

/** The server derives this compact field from frozen inputs, never task names. */
export function publicationKindLabel(kind: PublicationKind | null | undefined, lang: "en" | "zh"): string | null {
  return kind && kind in keys ? modelsPublishMessages[lang][keys[kind]] : null;
}
