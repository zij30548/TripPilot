import type { DialogueSelectionContext } from "./assistant";
import type { DialogueAction } from "./dialogue-action";

/** An ordinal belongs to the list shown when the user asked, not to an
 * action-specific filtered collection. Resolve it once to an ID; the domain
 * controller still checks current membership, role and confirmation authority.
 */
export function bindDialogueSelection(action: DialogueAction, context?: DialogueSelectionContext): DialogueAction | null {
  const selector = action.arguments.selector;
  if (typeof selector !== "string" || !/^[1-9]\d*$/.test(selector)) return action;
  const type = ["read_saved", "restore_saved"].includes(action.name) ? "saved" :
    ["remove_place", "exclude_candidate", "restore_candidate", "set_stay", "set_visit_mode", "preview_exclusion"].includes(action.name) ||
    action.name === "set_cost" && action.arguments.kind === "ticket" ? "place" : null;
  // Meal ordinals explicitly denote trip dates, not arbitrary displayed POIs.
  if (!type) return action;
  if (!context) return null;
  const exactId = context.items.filter(row => row.type === type && row.id === selector);
  const rows = exactId.length ? exactId : context.items.filter(row => row.type === type && row.ordinal === Number(selector));
  if (rows.length !== 1) return null;
  return { ...action, arguments: { ...action.arguments, selector: rows[0].id } };
}
