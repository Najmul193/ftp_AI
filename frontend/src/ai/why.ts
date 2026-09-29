/** "Why?" on a dashboard tile: a ready-made Ask FTP question for that figure.
 *
 *  Each figure maps to the breakdown that explains it -- profit to its volume
 *  and rate effects, a deposit rate to the deposit products that moved it, a
 *  bank-wide ratio to the divisions -- run on the page's own filters. The plan
 *  is built here, not by a model, so the question is exact and the answer
 *  needs only the narration.
 *
 *  Returns undefined when AI is off or the reader may not use Ask FTP, so a
 *  tile shows no button at all. */

import { useApp } from "../state";
import { openAsk } from "./Ask";

export type WhyKey =
  | "net_ftp_profit" | "asset_ftp_profit" | "liability_ftp_profit" | "ftp_yield"
  | "yield_on_advances" | "cost_of_deposits" | "spread" | "nim" | "casa" | "cd_ratio";

const WHY: Record<WhyKey, { q: string; plan: Record<string, unknown> }> = {
  net_ftp_profit: { q: "Why did net FTP profit change?",
    plan: { tool: "why", by: "product", limit: 10, title: "What moved net FTP profit" } },
  asset_ftp_profit: { q: "Why did FTP profit on loans change?",
    plan: { tool: "why", by: "product", side: "ASSET", limit: 10, title: "What moved lending FTP profit" } },
  liability_ftp_profit: { q: "Why did FTP profit on deposits change?",
    plan: { tool: "why", by: "product", side: "LIABILITY", limit: 10, title: "What moved deposit FTP profit" } },
  ftp_yield: { q: "Which products moved FTP yield?",
    plan: { tool: "compare", metrics: ["ftp_yield"], by: "product", compare: true,
            sort: "change:ftp_yield", order: "asc", limit: 10, title: "FTP yield by product" } },
  yield_on_advances: { q: "Which loan products moved the yield on advances?",
    plan: { tool: "compare", metrics: ["yield_on_advances", "advances"], by: "product", side: "ASSET",
            compare: true, sort: "change:yield_on_advances", order: "asc", limit: 10,
            title: "Yield on advances by loan product" } },
  cost_of_deposits: { q: "Which deposit products moved the cost of deposits?",
    plan: { tool: "compare", metrics: ["cost_of_deposits", "deposits"], by: "product", side: "LIABILITY",
            compare: true, sort: "change:cost_of_deposits", order: "desc", limit: 10,
            title: "Cost of deposits by deposit product" } },
  spread: { q: "Where did the gross spread move?",
    plan: { tool: "compare", metrics: ["spread", "yield_on_advances", "cost_of_deposits"],
            by: "division", compare: true, sort: "change:spread", order: "asc",
            title: "Gross spread by division" } },
  nim: { q: "Where did the net interest margin move?",
    plan: { tool: "compare", metrics: ["nim"], by: "division", compare: true, sort: "change:nim",
            order: "asc", title: "Net interest margin by division" } },
  casa: { q: "Which deposit products moved the deposit mix?",
    plan: { tool: "compare", metrics: ["deposits"], by: "product", side: "LIABILITY", compare: true,
            sort: "change:deposits", order: "desc", limit: 12, title: "Deposits by product" } },
  cd_ratio: { q: "Where did loans and deposits move?",
    plan: { tool: "compare", metrics: ["advances", "deposits"], by: "division", compare: true,
            sort: "change:advances", order: "desc", title: "Loans and deposits by division" } },
};

export function useWhy() {
  const { ai, filters } = useApp();
  return (key: WhyKey): (() => void) | undefined => {
    if (!ai?.enabled || !ai.can_chat) return undefined;
    const w = WHY[key];
    return () => openAsk(w.q, { plan: w.plan, filters: { ...filters } });
  };
}
