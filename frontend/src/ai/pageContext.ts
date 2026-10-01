/** What a page adds to Ask FTP's sense of where the asker is.
 *
 *  The page id and the filter bar are read directly; this carries what only a
 *  page knows -- which branch the coach has open. A page sets it while mounted
 *  and clears it on the way out, so a question never inherits a stale branch. */

export interface PageExtra {
  branch?: string; branchLabel?: string;
  /** The scenario lab's settings on screen, and a few words for the context line. */
  scenario?: Record<string, unknown>; scenarioLabel?: string;
  /** The market rate explorer's selection, and a few words for the context line. */
  market?: { book: string; peers: string; product?: string; bank?: string; basis?: string }; marketLabel?: string;
}

let extra: PageExtra = {};

export function setPageExtra(x: PageExtra) { extra = x; }
export function clearPageExtra() { extra = {}; }
export function pageExtra(): PageExtra { return extra; }

/** Page names as Ask FTP shows them in its context line. */
export const PAGE_LABELS: Record<string, string> = {
  basic: "Basic overview", daily: "Daily", overview: "Overview", analytics: "Analytics",
  leaders: "Leaders", accounts: "Accounts", consolidated: "Consolidated", intel: "Intelligence",
  coach: "Branch coach", outlook: "Outlook", scenario: "Scenario lab", pulse: "Bank pulse", market: "Market rates", rates: "Rate configuration", upload: "Upload", admin: "Master data",
  activity: "Activity log", ai: "AI management",
};
