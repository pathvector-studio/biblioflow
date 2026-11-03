export type PaperStatus = "unread" | "reading" | "read" | "wishlist";
export const PAPER_STATUSES: PaperStatus[] = ["unread", "reading", "read", "wishlist"];

export type Role = "viewer" | "editor" | "owner";

export interface Author {
  family: string;
  given?: string | null;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
}

export interface MeResponse {
  user: User;
  auth_method: "session" | "pat";
  scopes: string[];
}

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  kind: "personal" | "shared";
  role: Role;
}

export interface PaperLight {
  id: string;
  identifier: string;
  citekey: string | null;
  title: string;
  authors: Author[];
  year: number | null;
  venue: string | null;
  tags: string[];
  status: PaperStatus;
  has_pdf: boolean;
  relative_file_path: string | null;
}

export interface PaperDetail extends PaperLight {
  workspace_id: string;
  doi: string | null;
  arxiv_id: string | null;
  abstract: string | null;
  bibtex: string | null;
  notes_md: string;
  collection_ids: string[];
  page_count: number | null;
  updated_at: string;
}

export interface PaperPatch {
  title?: string;
  authors?: Author[];
  year?: number | null;
  venue?: string | null;
  abstract?: string | null;
  status?: PaperStatus;
  citekey?: string;
  notes_md?: string;
  tags?: string[];
  collection_ids?: string[];
}

export interface LookupItem {
  identifier: string;
  identifier_type: "arxiv" | "doi" | "internal";
  title: string;
  authors: Author[];
  year: number | null;
  venue: string | null;
  bibtex: string;
}

export interface LookupResult {
  query: string;
  kind: "arxiv" | "doi" | "title";
  results: LookupItem[];
}

export interface Tag {
  id: string;
  name: string;
  color: string | null;
  paper_count: number;
}

export interface Collection {
  id: string;
  workspace_id: string;
  name: string;
  parent_id: string | null;
  paper_count: number;
}

export interface ApiToken {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface CreatedApiToken extends ApiToken {
  token: string;
}

export type AnnotationKind = "highlight" | "area" | "note";

export interface Annotation {
  id: string;
  paper_id: string;
  user_id: string;
  user_display_name: string;
  kind: AnnotationKind;
  page_number: number;
  position: unknown;
  content_text: string | null;
  comment: string | null;
  color: string | null;
  created_at: string;
}

export interface AnnotationDraft {
  kind: AnnotationKind;
  page_number: number;
  position: unknown;
  content_text: string | null;
  comment: string;
  color: string;
}
