type Listener = (event: MessageEvent<string>) => void;

/** In-memory EventSource for tests: `FakeEventSource.last()?.emit("kind", data)`. */
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readyState = FakeEventSource.OPEN;
  onmessage: Listener | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  private readonly listeners = new Map<string, Set<Listener>>();

  constructor(url: string | URL) {
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  /** The most recent instance, optionally the latest one for `url`. */
  static last(url?: string): FakeEventSource | undefined {
    return FakeEventSource.instances.filter((s) => url == null || s.url === url).at(-1);
  }

  static reset(): void {
    FakeEventSource.instances = [];
  }

  get closed(): boolean {
    return this.readyState === FakeEventSource.CLOSED;
  }

  addEventListener(name: string, listener: Listener): void {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name)!.add(listener);
  }

  removeEventListener(name: string, listener: Listener): void {
    this.listeners.get(name)?.delete(listener);
  }

  /** Dispatches a named event; objects are sent as JSON. Ignored once closed. */
  emit(name: string, data: unknown, id?: string): void {
    if (this.closed) return;
    const event = new MessageEvent(name, {
      data: typeof data === "string" ? data : JSON.stringify(data),
      lastEventId: id ?? "",
    });
    if (name === "message") this.onmessage?.(event);
    for (const listener of this.listeners.get(name) ?? []) listener(event);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }
}
