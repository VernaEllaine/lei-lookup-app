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

export interface PaginatedResponse {
  rows: RowResult[];
  total: number;
  page: number;
  page_size: number;
  summary: Summary;
}

export interface UploadResponse {
  session_id: string;
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

// ---------------------------------------------------------------------------
// LEI Validation (bulk LEI checking) types
// ---------------------------------------------------------------------------

export interface ValidationRowResult {
  index: number;
  entity_name: string;
  provided_lei: string;
  entity_status: string;
  registration_status: string;
  flag: string;
  suggested_lei: string;
  suggested_legal_name: string;
  suggested_confidence: string;
}

export interface ValidationSummary {
  ok: number;
  lapsed: number;
  invalid: number;
  not_found: number;
  errors: number;
  total: number;
}

export interface ValidationProgress {
  index: number;
  total: number;
  entity_name: string;
  row: ValidationRowResult;
  done: boolean;
}

export interface ValidationUploadResponse {
  session_id: string;
  headers: string[];
  row_count: number;
  detected_entity_column: string | null;
  detected_lei_column: string | null;
}

export interface ValidationPaginatedResponse {
  rows: ValidationRowResult[];
  total: number;
  page: number;
  page_size: number;
  summary: ValidationSummary;
}

// ---------------------------------------------------------------------------
// Manual Lookup types (direct company-name search without CSV)
// ---------------------------------------------------------------------------

export interface ManualLookupResult {
  query: string;
  lei: string;
  legal_name: string;
  jurisdiction: string;
  status: string;
  confidence: string;
  match_status: string;
  candidates: string;
  error: string;
}

export interface ManualLookupResponse {
  results: ManualLookupResult[];
}

// ---------------------------------------------------------------------------
// Manual Validation types (direct LEI validation without CSV)
// ---------------------------------------------------------------------------

export interface ManualValidationResult {
  entity_name: string;
  lei: string;
  entity_status: string;
  registration_status: string;
  legal_name: string;
  jurisdiction: string;
  flag: string;
  error: string;
}

export interface ManualValidationResponse {
  results: ManualValidationResult[];
}

// ---------------------------------------------------------------------------
// ISIN Lookup types
// ---------------------------------------------------------------------------

export interface IsinResult {
  isin: string;
  lei: string;
  legal_name: string;
  country: string;
  entity_status: string;
  registration_status: string;
  error: string;
}

export interface IsinLookupResponse {
  results: IsinResult[];
}

export interface LeiDetail {
  lei: string;
  legal_name: string;
  country: string;   // ISO 3166-1 alpha-2, legal address
  region: string;    // "Europe" | "Global" | ""
  entity_status: string;
  registration_status: string;
  error: string;
}

export interface LeiDetailsResponse {
  results: LeiDetail[];
}

export interface GleifLocalStatus {
  available: boolean;
  publish_date?: string;
  lei_count?: string;
  isin_count?: string;
  loaded_at?: string;
}
