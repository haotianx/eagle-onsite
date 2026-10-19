import { renderHook } from "@testing-library/react";
import { act } from "react";
import { describe, expect, it, vi } from "vitest";

import { FakeEventSource } from "@/test/utils";

import { useEventSource } from "./sse";

describe("useEventSource", () => {
  it("delivers named JSON events and closes on terminal events", () => {
    const onEvent = vi.fn();
    renderHook(() => useEventSource("/api/runs/1/events", onEvent, { events: ["node_started", "run_finished"], closeOn: ["run_finished"] }));
    const source = FakeEventSource.last("/api/runs/1/events")!;
    act(() => source.emit("node_started", { id: 1, kind: "node_started", payload: { node: "parse" } }));
    act(() => source.emit("other", {}));
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0].slice(0, 2)).toEqual(["node_started", { id: 1, kind: "node_started", payload: { node: "parse" } }]);
    act(() => source.emit("run_finished", { payload: { status: "succeeded" } }));
    expect(source.closed).toBe(true);
  });

  it("does nothing without a url and closes on unmount", () => {
    const { rerender, unmount } = renderHook(({ url }) => useEventSource(url, () => {}, { events: ["x"] }), {
      initialProps: { url: null as string | null },
    });
    expect(FakeEventSource.instances).toHaveLength(0);
    rerender({ url: "/api/events" });
    const source = FakeEventSource.last()!;
    unmount();
    expect(source.closed).toBe(true);
  });
});
