// Which side of the screen the panel and its "I" handle sit on.
// Quick Access is a window wider than its visible part (only its left edge shows), so anything
// anchored to its right edge is off screen: there the panel is always on the left, whatever
// the "Panel side" setting says. The setting only applies to the library.

export type Side = "auto" | "left" | "right";

export function panelOnLeft(role: string, side: Side): boolean {
  return role === "quickaccess" || side === "left";
}
