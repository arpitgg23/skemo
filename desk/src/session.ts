import { useMutation, useQuery } from "@tanstack/react-query";
import { ApiError, api, type Action, type CurrentUser, type DocType, type Session } from "./api";

/** Who's signed in. A 401 means "not signed in", which isn't an error worth retrying. */
export function useSession() {
  const { data, isLoading, error } = useQuery<Session | null>({
    queryKey: ["me"],
    queryFn: api.me,
    retry: false,
    staleTime: 60_000,
  });
  const signedOut = error instanceof ApiError && error.status === 401;
  return {
    // A failed refetch keeps the last data, so a 401 (signed out, or the session ran out
    // while the tab was open) has to win over it.
    user: signedOut ? undefined : data?.user,
    site: signedOut ? undefined : data?.site,
    developerMode: Boolean(!signedOut && data?.developer_mode),
    isLoading,
    signedOut,
    // A real problem, like the API being down — not just "please sign in".
    error: signedOut ? undefined : error,
  };
}

export function useLogout() {
  return useMutation({
    mutationFn: api.logout,
    // Start again at the home page with nothing cached: everything held in memory
    // belonged to the person signing out.
    onSettled: () => window.location.assign("/"),
  });
}

/** What the user may do with a DocType. Missing means they can't even see it. */
export const allowed = (doctype: DocType | undefined, action: Action): boolean =>
  Boolean(doctype?.can?.includes(action));

export type { CurrentUser };
