import { describe, expect, it } from "vitest";

import { regionToRect } from "./regions";

describe("regionToRect", () => {
  it("scales points to pixels with a top-left origin", () => {
    const rect = regionToRect({ page: 1, x0: 72, y0: 100, x1: 144, y1: 150 }, 612, 918);
    expect(rect).toEqual({ left: 108, top: 150, width: 108, height: 75 });
  });

  it("normalizes swapped corners", () => {
    const rect = regionToRect({ page: 2, x0: 144, y0: 150, x1: 72, y1: 100 }, 612, 612);
    expect(rect).toEqual({ left: 72, top: 100, width: 72, height: 50 });
  });
});
