// Scroll `container` just enough to show `el` (vertically or horizontally).
// Plain scrollTop/scrollLeft maths instead of scrollIntoView options, so behaviour
// doesn't depend on the Chromium version Steam ships.
export function keepVisible(container: HTMLElement, el: HTMLElement, axis: "y" | "x" = "y", margin = 0) {
  if (!container.contains(el)) return;
  const view = container.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  // An element taller (wider) than the view can't be shown whole: show its start, never
  // jump to its end (e.g. a long image grid getting the ring).
  if (axis === "y") {
    const tooBig = r.height > view.height - 2 * margin;
    if (r.top < view.top + margin || tooBig) container.scrollTop -= view.top + margin - r.top;
    else if (r.bottom > view.bottom - margin) container.scrollTop += r.bottom - (view.bottom - margin);
  } else {
    const tooBig = r.width > view.width - 2 * margin;
    if (r.left < view.left + margin || tooBig) container.scrollLeft -= view.left + margin - r.left;
    else if (r.right > view.right - margin) container.scrollLeft += r.right - (view.right - margin);
  }
}
