import {
  ArtifactListResponse,
  ArtifactView,
  EventListResponse,
  IssueListResponse,
  MeResponse,
  RepositoryListResponse,
  TaskListResponse,
  TaskResponse,
  TokenResponse,
} from "@/types";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001/api/v1";

const HEALTH_URL =
  process.env.NEXT_PUBLIC_HEALTH_URL || "http://localhost:8001/health";


const TOKEN_KEY = "gitresolve_access_token";
const USER_KEY = "gitresolve_user_meta";

export class ApiError extends Error {
  code: string;
  status: number;
  details?: Record<string, unknown>;

  constructor(message: string, code: string, status: number, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export function getAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(TOKEN_KEY, token);
  window.dispatchEvent(new Event("auth_change"));
}

export function clearAuthToken(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
  window.dispatchEvent(new Event("auth_change"));
}

export function getStoredUser(): { userId: string; githubLogin: string } | null {
  if (typeof window === "undefined") return null;
  const raw = sessionStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function setStoredUser(user: { userId: string; githubLogin: string }): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
}

async function request<T>(
  endpoint: string,
  options: RequestInit = {}
): Promise<T> {
  const token = getAuthToken();
  const headers = new Headers(options.headers || {});

  if (!headers.has("Content-Type") && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }

  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const url = `${API_BASE_URL}${endpoint.startsWith("/") ? endpoint : `/${endpoint}`}`;

  let res: Response;
  try {
    res = await fetch(url, { ...options, headers });
  } catch (err: unknown) {
    throw new ApiError(
      "Cannot connect to GitResolve backend server at " + API_BASE_URL,
      "network_error",
      0,
      { originalError: err instanceof Error ? err.message : String(err) }
    );
  }

  if (res.status === 401) {
    clearAuthToken();
    throw new ApiError("Session expired or unauthorized.", "unauthorized", 401);
  }

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    body = null;
  }

  if (!res.ok) {
    const errorPayload = body as { error?: { code?: string; message?: string; details?: Record<string, unknown> } };
    const errObj = errorPayload?.error;
    throw new ApiError(
      errObj?.message || `Request failed with status ${res.status}`,
      errObj?.code || "request_failed",
      res.status,
      errObj?.details
    );
  }

  return body as T;
}

// --- Auth Endpoints ---

export async function loginWithDevToken(
  userId = "123456",
  githubLogin = "dev_user"
): Promise<TokenResponse> {
  const data = await request<TokenResponse>("/auth/dev-token", {
    method: "POST",
    body: JSON.stringify({ userId, githubLogin }),
  });
  setAuthToken(data.accessToken);
  setStoredUser({ userId, githubLogin });
  return data;
}

export async function ensureDevAuthenticated(): Promise<string> {
  const existing = getAuthToken();
  if (existing) return existing;
  const storedUser = getStoredUser();
  const data = await loginWithDevToken(
    storedUser?.userId || "123456",
    storedUser?.githubLogin || "dev_aakash"
  );
  return data.accessToken;
}

export async function getMe(): Promise<MeResponse> {
  return request<MeResponse>("/me");
}

// --- Repository & Issue Endpoints ---

export async function listRepositories(): Promise<RepositoryListResponse> {
  return request<RepositoryListResponse>("/repositories");
}

export async function listIssues(repoId: string): Promise<IssueListResponse> {
  return request<IssueListResponse>(`/repositories/${encodeURIComponent(repoId)}/issues?state=open`);
}

// --- Task Lifecycle Endpoints ---

export async function createTask(
  repoId: string,
  issueNumber: number,
  idempotencyKey?: string
): Promise<TaskResponse> {
  const headers: Record<string, string> = {};
  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }

  return request<TaskResponse>("/tasks", {
    method: "POST",
    headers,
    body: JSON.stringify({ repoId, issueNumber }),
  });
}

export async function listTasks(
  limit = 20,
  cursor?: string | null
): Promise<TaskListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return request<TaskListResponse>(`/tasks?${params.toString()}`);
}

export async function getTask(taskId: string): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}`);
}

export async function startTask(taskId: string): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}/start`, {
    method: "POST",
  });
}

export async function cancelTask(taskId: string): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}/cancel`, {
    method: "POST",
  });
}

export async function approveTask(
  taskId: string,
  diffHash: string
): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}/approve`, {
    method: "POST",
    body: JSON.stringify({ diffHash }),
  });
}

export async function rejectTask(taskId: string): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}/reject`, {
    method: "POST",
  });
}

// --- Events & Artifacts Endpoints ---

export async function listEvents(
  taskId: string,
  after = 0,
  limit = 50
): Promise<EventListResponse> {
  return request<EventListResponse>(
    `/tasks/${taskId}/events?after=${after}&limit=${limit}`
  );
}

export async function listArtifacts(taskId: string): Promise<ArtifactListResponse> {
  return request<ArtifactListResponse>(`/tasks/${taskId}/artifacts`);
}

export async function getArtifact(
  taskId: string,
  artifactId: string
): Promise<ArtifactView> {
  return request<ArtifactView>(`/tasks/${taskId}/artifacts/${artifactId}`);
}

// --- Health ---

export async function checkBackendHealth(): Promise<{ status: string }> {
  try {
    const res = await fetch(HEALTH_URL);
    if (!res.ok) return { status: "unhealthy" };
    return await res.json();
  } catch {
    return { status: "disconnected" };
  }
}
