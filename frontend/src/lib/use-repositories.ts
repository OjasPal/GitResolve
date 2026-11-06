"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiQueryClient } from "./query-client";
import { repositoryQuery } from "./api";
import type { RepositoryListResponse } from "@/types";

export function useRepositories() {
  const options = repositoryQuery();
  const scope = String(options.queryKey[1]);
  const first = useQuery(options);
  const [extra, setExtra] = useState<{scope: string; pages: RepositoryListResponse[]}>({scope, pages: []});
  const [loadingMore, setLoadingMore] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const pages = extra.scope === scope ? extra.pages : [];
  const nextPage = pages.length ? pages[pages.length - 1].nextPage : first.data?.nextPage;
  const repositories = [...new Map([...(first.data?.items || []), ...pages.flatMap(p => p.items)].map(r => [r.id, r])).values()];
  const loadMore = async () => {
    if (!nextPage || loadingMore) return;
    setLoadingMore(true); setPageError(null);
    try {
      const page = await apiQueryClient.fetchQuery(repositoryQuery(nextPage));
      setExtra(old => ({scope, pages: [...(old.scope === scope ? old.pages : []), page]}));
    } catch (error) { setPageError(error instanceof Error ? error.message : "Unable to load more repositories."); }
    finally { setLoadingMore(false); }
  };
  return { repositories, loading: first.isPending, refreshing: first.isFetching,
    error: first.error?.message || pageError, loadingMore, hasMore: Boolean(nextPage), loadMore,
    refresh: () => { setExtra({scope, pages: []}); return first.refetch(); } };
}
