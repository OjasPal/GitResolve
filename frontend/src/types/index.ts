// Shared frontend TypeScript types matching the OpenAPI 3.1.0 contract

export type TaskStatus =
  // Active
  | "queued"
  | "planning"
  | "running"
  | "creating_pr"
  // Human Gates
  | "awaiting_start"
  | "awaiting_approval"
  // Terminal Success
  | "completed"
  // Terminal Stopped
  | "needs_input"
  | "rejected"
  | "cancelled"
  // Failure
  | "failed_not_reproduced"
  | "failed_no_fix"
  | "failed_policy"
  | "failed_budget"
  | "timed_out"
  | "failed_infra"
  | "worker_lost";

export type TaskStage =
  | "intake"
  | "plan"
  | "setup"
  | "baseline"
  | "locate"
  | "reproduce"
  | "patch"
  | "test"
  | "evaluate"
  | "policy_gate"
  | "awaiting_approval"
  | "pr"
  | "completed";

export type Action =
  | "start"
  | "cancel"
  | "approve"
  | "reject"
  | "retry_pr"
  | "run_again";

export type FailureCode =
  | "issue_ambiguous"
  | "not_reproduced"
  | "no_fix_found"
  | "policy_violation"
  | "budget_exceeded"
  | "timeout"
  | "github_rate_limited"
  | "github_error"
  | "access_revoked"
  | "llm_unavailable"
  | "sandbox_error"
  | "internal_error"
  | "worker_lost";

export type EventLevel = "info" | "warn" | "error";

export type EventType =
  | "status_change"
  | "stage_change"
  | "tool_call"
  | "llm_call"
  | "test_run"
  | "policy_check"
  | "attempt"
  | "approval"
  | "pr_created"
  | "cancellation"
  | "error"
  | "info";

export type ApprovalState = "pending" | "approved" | "rejected";

export type ArtifactType =
  | "plan"
  | "repro_test"
  | "attempt_diff"
  | "attempt_tests"
  | "final_diff"
  | "final_tests";

export interface PlanData {
  problemSummary: string;
  steps: string[];
  likelyFiles: string[];
  questions?: string[];
}

export interface IssueComment {
  author?: string | null;
  body: string;
}

export interface IssueView {
  number: number;
  title?: string | null;
  url?: string | null;
  body?: string | null;
  bodyTruncated?: boolean;
  labels?: string[];
  comments?: IssueComment[];
}

export interface IssueSummary {
  number: number;
  title?: string | null;
  url?: string | null;
  labels?: string[];
}

export interface Attempts {
  used: number;
  max: number;
}

export interface Budgets {
  modelCalls: number;
  inputTokens: number;
  outputTokens: number;
  execSeconds: number;
}

export interface Limits {
  maxRepairAttempts: number;
  maxModelCalls: number;
  taskTimeoutSeconds: number;
  executionTimeoutSeconds: number;
  maxDiffFiles: number;
  maxDiffLines: number;
}

export interface Failure {
  code: FailureCode;
  message: string;
  phase?: string | null;
  retryable?: boolean;
}

export interface Approval {
  state: ApprovalState;
  by?: string | null;
  at?: string | null;
  diffHash?: string | null;
}

export interface PullRequestView {
  number: number;
  url: string;
  branch: string;
  createdAt?: string | null;
}

export interface TaskResponse {
  id: string;
  status: TaskStatus;
  stage: TaskStage;
  repoId: string;
  repoFullName: string;
  defaultBranch: string;
  baseSha?: string | null;
  issue: IssueView;
  plan?: PlanData | null;
  reproduction?: Record<string, unknown> | null;
  baseline?: Record<string, unknown> | null;
  attempts: Attempts;
  budgets: Budgets;
  limits: Limits;
  finalDiffHash?: string | null;
  finalDiffSummary?: {
    filesChanged?: number;
    insertions?: number;
    deletions?: number;
  } | null;
  approval?: Approval | null;
  failure?: Failure | null;
  result?: Record<string, unknown> | null;
  pr?: PullRequestView | null;
  cancelRequested: boolean;
  availableActions: Action[];
  createdAt: string;
  updatedAt: string;
  statusUpdatedAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  deadlineAt?: string | null;
}

export interface TaskSummaryItem {
  id: string;
  status: TaskStatus;
  stage: TaskStage;
  repoId: string;
  repoFullName: string;
  issue: IssueSummary;
  attempts: Attempts;
  pr?: PullRequestView | null;
  createdAt: string;
  updatedAt: string;
  finishedAt?: string | null;
}

export interface TaskListResponse {
  items: TaskSummaryItem[];
  nextCursor?: string | null;
}

export interface EventView {
  seq: number;
  ts: string;
  stage: TaskStage;
  type: EventType;
  level: EventLevel;
  message: string;
  data?: Record<string, unknown>;
}

export interface EventListResponse {
  items: EventView[];
  nextAfter: number;
}

export interface ArtifactSummary {
  id: string;
  type: ArtifactType;
  attempt?: number | null;
  contentType: string;
  sizeBytes: number;
  truncated: boolean;
  sha256: string;
  createdAt: string;
}

export interface ArtifactView extends ArtifactSummary {
  content: string;
}

export interface ArtifactListResponse {
  items: ArtifactSummary[];
}

export interface RepositoryView {
  id: string;
  fullName: string;
  defaultBranch: string;
  private: boolean;
  language?: string | null;
}

export interface RepositoryListResponse {
  items: RepositoryView[];
}

export interface IssueListItem {
  number: number;
  title: string;
  url: string;
  labels: string[];
}

export interface IssueListResponse {
  items: IssueListItem[];
}

export interface MeResponse {
  id: string;
  githubLogin: string;
  avatarUrl?: string | null;
  activeTaskId?: string | null;
}

export interface TokenResponse {
  accessToken: string;
  tokenType: string;
  expiresAt: string;
}

export interface ErrorResponse {
  error: {
    code: string;
    message: string;
    details?: Record<string, unknown>;
  };
}
