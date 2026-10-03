// Accent colours of the overlay (Settings › Panel › Accent color): selection ring,
// active tabs, switches… and the "I" handle. Names must match the backend's
// config.ACCENT_COLORS; "fg" is the text colour readable on top of the accent.
export const ACCENT_COLORS = [
  { value: "blue", label: "Blue", hex: "#1a9fff", rgb: "26, 159, 255", fg: "#ffffff" },
  { value: "yellow", label: "Yellow", hex: "#f5c400", rgb: "245, 196, 0", fg: "#1b2029" },
  { value: "green", label: "Green", hex: "#3fb950", rgb: "63, 185, 80", fg: "#ffffff" },
  { value: "red", label: "Red", hex: "#f04848", rgb: "240, 72, 72", fg: "#ffffff" },
  { value: "purple", label: "Purple", hex: "#a371f7", rgb: "163, 113, 247", fg: "#ffffff" },
  { value: "white", label: "White", hex: "#e6e9ef", rgb: "230, 233, 239", fg: "#1b2029" },
] as const;

export function accentColor(name: string | undefined) {
  return ACCENT_COLORS.find((c) => c.value === name) ?? ACCENT_COLORS[0];
}
