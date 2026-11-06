import { QueryClient } from "@tanstack/react-query";

// No server render performs authenticated requests. All cached data is memory-only.
export const apiQueryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, gcTime: 60_000 } },
});
