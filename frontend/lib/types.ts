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
  is_admin: boolean;
}

export interface SearchStatus {
  provider: "none" | "local" | "voyage" | "openai";
  model_state: "disabled" | "absent" | "loading" | "ready" | "unreachable";
  chunks_total: number;
  chunks_embedded: number;
  degraded: boolean;
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

export interface Member {
  user_id: string;
  email: string;
  display_name: string;
  role: Role;
}

export interface WorkspaceDetail extends Workspace {
  members: Member[];
}

export interface MyInvitation {
  id: string;
  workspace_name: string;
  role: Role;
  invited_by_name: string;
  expires_at: string;
}

export interface WorkspaceInvitation {
  id: string;
  email: string;
  role: Role;
  created_at: string;
  expires_at: string;
}

export type ExportFormat = "bibtex" | "ris" | "csljson";

export interface AutoExport {
  id: string;
  workspace_id: string;
  filename: string;
  format: ExportFormat;
  path: string;
  q: string | null;
  tag: string | null;
  collection_id: string | null;
  year: number | null;
  status: PaperStatus | null;
  paper_count: number | null;
  last_written_at: string | null;
  last_error: string | null;
  created_at: string;
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

// ---- AI chat (backend/app/schemas/chat.py) ----
export interface ChatStatus {
  enabled: boolean;
  provider: string;
  model: string | null;
  vision_available: boolean;
  daily_requests_remaining: number | null;
}

/** PDF のポイント単位、原点は左下（y1 < y2）。width / height はそのページの大きさ */
export interface PdfBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  width: number;
  height: number;
}

export interface PaperChunks {
  paper_id: string;
  page_from: number;
  page_to: number;
  chunks: { id: string; chunk_index: number; page_number: number; section: string | null; text: string; bboxes: PdfBox[] }[];
}

export interface Citation {
  ref: number;
  chunk_id: string;
  paper_id: string;
  page_number: number;
  section: string | null;
  bboxes: PdfBox[];
  label: string;
  /** 論文のタイトル（サーバーが付ける。古い会話にはないことがある） */
  title?: string;
}

export interface ChatSource {
  ref: number;
  chunk_id: string;
  paper_id: string;
  title: string;
  page_number: number;
  section: string | null;
  bboxes: PdfBox[];
}

export type ChatMessageStatus = "complete" | "error" | "cancelled";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  meta: Record<string, unknown> | null;
  status: ChatMessageStatus;
  created_at: string;
}

export interface Conversation {
  id: string;
  workspace_id: string;
  paper_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ChatMessage[];
}

export interface ImportItemResult {
  ref: string;
  title: string;
  status: "new" | "matched" | "unreadable";
  identifier: string | null;
  matched_by: "existing" | "file" | null;
  reason: string | null;
}

export interface ImportSummary {
  format: "bibtex" | "ris" | "csljson" | "zotero_rdf";
  dry_run: boolean;
  total: number;
  new: number;
  matched: number;
  unreadable: number;
  with_attachments: number;
  tags_added: number;
  collections_created: number;
  collection_links: number;
  notes_added: number;
  problems: ImportItemResult[];
  items: ImportItemResult[];
  problems_truncated: boolean;
}
