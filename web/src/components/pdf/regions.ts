/** A rectangle on a PDF page in PDF points with a top-left origin; pages are 1-based. */
export interface Region {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Scales a region from PDF points to CSS pixels on a page rendered `renderedWidthPx` wide. */
export function regionToRect(region: Region, pageWidthPts: number, renderedWidthPx: number): Rect {
  const scale = renderedWidthPx / pageWidthPts;
  const left = Math.min(region.x0, region.x1);
  const top = Math.min(region.y0, region.y1);
  return {
    left: left * scale,
    top: top * scale,
    width: Math.abs(region.x1 - region.x0) * scale,
    height: Math.abs(region.y1 - region.y0) * scale,
  };
}
