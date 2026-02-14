export interface RowResult {
  index: number;
  entity_name: string;
  lei: string;
  legal_name: string;
  jurisdiction: string;
  status: string;
  confidence: string;
  match_status: string;
  candidates: string;
  original: Record<string, string>;
}

export interface Summary {
  auto_matched: number;
  confirmed: number;
  reviewed: number;
  review_needed: number;
  no_match: number;
  errors: number;
  total: number;
  cache_size: number;
}

export interface UploadResponse {
  headers: string[];
  row_count: number;
  detected_column: string | null;
}

export interface LookupProgress {
  index: number;
  total: number;
  company: string;
  row: RowResult;
  done: boolean;
}

export interface ValidateLeiResponse {
  valid: boolean;
  legal_name: string;
  jurisdiction: string;
  status: string;
  needs_confirmation: boolean;
  message: string;
}
