export type ScheduleTimer<Timer> = (
  callback: () => void,
  delay: number
) => Timer;

export class PerRootDebouncer<Timer> {
  private readonly timers = new Map<string, Timer>();

  constructor(
    private readonly scheduleTimer: ScheduleTimer<Timer>,
    private readonly clearTimer: (timer: Timer) => void
  ) {}

  schedule(root: string, delay: number, callback: () => void): void {
    const existing = this.timers.get(root);
    if (existing !== undefined) {
      this.clearTimer(existing);
    }

    let timer!: Timer;
    timer = this.scheduleTimer(() => {
      if (this.timers.get(root) !== timer) {
        return;
      }
      this.timers.delete(root);
      callback();
    }, delay);
    this.timers.set(root, timer);
  }

  clear(): void {
    for (const timer of this.timers.values()) {
      this.clearTimer(timer);
    }
    this.timers.clear();
  }
}

interface RootStatusState {
  active: boolean;
  trailing: boolean;
  trailingRevision: number | undefined;
}

export class StatusUpdateCoordinator<Result> {
  private readonly roots = new Map<string, RootStatusState>();
  private disposed = false;
  private visibleRoot: string | undefined;
  private visibleRevision = 0;

  constructor(
    private readonly start: (
      root: string,
      complete: (result: Result) => void
    ) => void,
    private readonly publish: (root: string, result: Result) => void
  ) {}

  show(root: string | undefined): void {
    if (this.disposed) {
      return;
    }
    this.visibleRoot = root;
    this.visibleRevision += 1;
    if (root !== undefined) {
      this.enqueue(root, this.visibleRevision);
    }
  }

  refresh(root: string): void {
    if (this.disposed || this.visibleRoot !== root) {
      return;
    }
    this.visibleRevision += 1;
    this.enqueue(root, this.visibleRevision);
  }

  clear(): void {
    this.disposed = true;
    this.visibleRoot = undefined;
    this.visibleRevision += 1;
    for (const state of this.roots.values()) {
      state.trailing = false;
      state.trailingRevision = undefined;
    }
  }

  private enqueue(root: string, revision: number | undefined): void {
    const state = this.roots.get(root);
    if (state?.active) {
      state.trailing = true;
      state.trailingRevision = revision;
      return;
    }

    const nextState = state ?? {
      active: false,
      trailing: false,
      trailingRevision: undefined,
    };
    this.roots.set(root, nextState);
    this.startRun(root, nextState, revision);
  }

  private startRun(
    root: string,
    state: RootStatusState,
    revision: number | undefined
  ): void {
    state.active = true;
    this.start(root, (result) => {
      state.active = false;

      if (
        revision !== undefined
        && revision === this.visibleRevision
        && root === this.visibleRoot
      ) {
        this.publish(root, result);
      }

      if (state.trailing) {
        const trailingRevision = state.trailingRevision;
        state.trailing = false;
        state.trailingRevision = undefined;
        if (
          trailingRevision !== undefined
          && trailingRevision === this.visibleRevision
          && root === this.visibleRoot
        ) {
          this.startRun(root, state, trailingRevision);
        } else {
          this.roots.delete(root);
        }
      } else {
        this.roots.delete(root);
      }
    });
  }
}
