import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

import { FakeEventSource } from "./fake-event-source";

const realFetch = globalThis.fetch;

// Browser APIs jsdom lacks.
class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}
vi.stubGlobal("ResizeObserver", NoopObserver);
vi.stubGlobal("IntersectionObserver", NoopObserver);
vi.stubGlobal("EventSource", FakeEventSource);
vi.stubGlobal(
  "matchMedia",
  (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener() {},
      removeListener() {},
      addEventListener() {},
      removeEventListener() {},
      dispatchEvent: () => false,
    }) as MediaQueryList,
);
window.scrollTo = () => {};
Element.prototype.scrollIntoView = function () {};
Element.prototype.hasPointerCapture = () => false;
Element.prototype.releasePointerCapture = function () {};

afterEach(() => {
  cleanup();
  FakeEventSource.reset();
  globalThis.fetch = realFetch;
});
