import {
  ArtifactListResponse,
  ArtifactView,
  EventListResponse,
  IssueListResponse,
  MeResponse,
  RepositoryListResponse,
  TaskListResponse,
  TaskResponse} from "@/types";
import { apiQueryClient } from "./query-client";

let cacheOwner: string | null = null;
let cacheGeneration = 0;
function queryScope() {
  const token = getAuthToken();
  if (token !== cacheOwner) {
    apiQueryClient.clear();
    cacheOwner = token;
    cacheGeneration += 1;
  }
  return cacheGeneration;
}

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000/api/v1";

const HEALTH_URL =
  process.env.NEXT_PUBLIC_HEALTH_URL || API_BASE_URL.replace(/\/api\/v1\/?$/, "/health");

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
  return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(TOKEN_KEY, token);
  queryScope();
  window.dispatchEvent(new Event("auth_change"));
}

export function clearAuthToken(): void {
  if (typeof window === "undefined") return;
  sessionStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
  localStorage.removeItem(USER_KEY);
  queryScope();
  window.dispatchEvent(new Event("auth_change"));
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
    if (getAuthToken() === token) clearAuthToken();
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

  if (getAuthToken() !== token) throw new ApiError("Account changed. Please retry.", "session_changed", 409);
  return body as T;
}

// GitHub authorization uses the backend's existing session contract.
export function connectGitHub(): void {
  // This route belongs to the external FastAPI server, not the Next.js router.
  const destination = new URL(`${API_BASE_URL}/auth/github/login`);
  window.location.assign(destination.href);
}

export async function getMe(): Promise<MeResponse> {
  return request<MeResponse>("/me");
}

// --- Repository & Issue Endpoints ---

export function repositoryQuery(page = 1) {
  return {
    queryKey: ["repositories", queryScope(), page],
    queryFn: () => request<RepositoryListResponse>(`/repositories?page=${page}`),
    staleTime: 15_000,
  };
}

export async function listRepositories(page = 1): Promise<RepositoryListResponse> {
  return apiQueryClient.ensureQueryData({ ...repositoryQuery(page), revalidateIfStale: true });
}

export function cachedRepositories(): RepositoryListResponse | undefined {
  return apiQueryClient.getQueryData(repositoryQuery().queryKey);
}

export async function listIssues(repoId: string, page = 1): Promise<IssueListResponse> {
  return apiQueryClient.fetchQuery({ queryKey: ["issues", queryScope(), repoId, page], staleTime: 15_000,
    queryFn: () => request<IssueListResponse>(`/repositories/${encodeURIComponent(repoId)}/issues?state=open&page=${page}`) });
}

// --- Task Lifecycle Endpoints ---

export async function createTask(
  repoId: string,
  source: number | string,
  idempotencyKey?: string,
  attachmentIds: string[] = []
): Promise<TaskResponse> {
  const headers: Record<string, string> = {};
  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }

  return request<TaskResponse>("/tasks", {
    method: "POST",
    headers,
    body: JSON.stringify(typeof source === "string" ? { repoId, prompt: source, attachmentIds } : { repoId, issueNumber: source, attachmentIds }),
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

export async function startTask(taskId: string, revision = 0): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}/start`, {
    method: "POST",
    body: JSON.stringify({ revision }),
  });
}

export async function cancelTask(taskId: string): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}/cancel`, {
    method: "POST",
  });
}

export async function approveTask(
  taskId: string,
  diffHash: string,
  revision = 0
): Promise<TaskResponse> {
  return request<TaskResponse>(`/tasks/${taskId}/approve`, {
    method: "POST",
    body: JSON.stringify({ diffHash, revision }),
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

export interface BackendHealth { status: string; worker?: { status: string; agentMode: string } }

export async function checkBackendHealth(): Promise<BackendHealth> {
  try {
    const res = await fetch(HEALTH_URL);
    if (!res.ok) return { status: "unhealthy" };
    return await res.json();
  } catch {
    return { status: "disconnected" };
  }
}

export function gateConversation(id:string,body:{message:string;operation:"ask"|"revise"|"prompt";revision:number;requestId:string}):Promise<TaskResponse>{return request(`/tasks/${id}/conversation`,{method:"POST",body:JSON.stringify(body)})}
