const assert = require("node:assert/strict");
const test = require("node:test");

const {
  PerRootDebouncer,
  StatusUpdateCoordinator,
} = require("../out/coordinators.js");

test("save debounce timers are independent per workspace root", () => {
  const scheduled = [];
  const cleared = [];
  const debouncer = new PerRootDebouncer(
    (callback, delay) => {
      const timer = { callback, delay };
      scheduled.push(timer);
      return timer;
    },
    (timer) => cleared.push(timer)
  );
  const refreshed = [];

  debouncer.schedule("/one", 100, () => refreshed.push("/one:first"));
  debouncer.schedule("/two", 200, () => refreshed.push("/two"));
  debouncer.schedule("/one", 300, () => refreshed.push("/one:latest"));

  assert.deepEqual(cleared, [scheduled[0]]);
  scheduled[1].callback();
  scheduled[2].callback();
  assert.deepEqual(refreshed, ["/two", "/one:latest"]);
});

test("a superseded save debounce timer cannot refresh when manually fired", () => {
  const scheduled = [];
  const debouncer = new PerRootDebouncer(
    (callback) => {
      const timer = { callback };
      scheduled.push(timer);
      return timer;
    },
    () => {}
  );
  const refreshed = [];

  debouncer.schedule("/one", 100, () => refreshed.push("stale"));
  debouncer.schedule("/one", 100, () => refreshed.push("current"));
  scheduled[0].callback();

  assert.deepEqual(refreshed, []);
});

test("clearing save debounce timers cancels every workspace root", () => {
  const scheduled = [];
  const cleared = [];
  const debouncer = new PerRootDebouncer(
    (callback) => {
      const timer = { callback };
      scheduled.push(timer);
      return timer;
    },
    (timer) => cleared.push(timer)
  );

  debouncer.schedule("/one", 100, () => {});
  debouncer.schedule("/two", 100, () => {});
  debouncer.clear();

  assert.deepEqual(cleared, scheduled);
});

test("a cleared save debounce timer cannot refresh when manually fired", () => {
  const scheduled = [];
  const debouncer = new PerRootDebouncer(
    (callback) => {
      const timer = { callback };
      scheduled.push(timer);
      return timer;
    },
    () => {}
  );
  const refreshed = [];

  debouncer.schedule("/one", 100, () => refreshed.push("/one"));
  debouncer.clear();
  scheduled[0].callback();

  assert.deepEqual(refreshed, []);
});

test("status events while active coalesce into one trailing update per root", () => {
  const runs = [];
  const published = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    (root, result) => published.push({ root, result })
  );

  coordinator.show("/one");
  coordinator.refresh("/one");
  coordinator.refresh("/one");

  assert.deepEqual(runs.map(({ root }) => root), ["/one"]);
  runs[0].complete("first");
  assert.deepEqual(runs.map(({ root }) => root), ["/one", "/one"]);
  runs[1].complete("trailing");
  assert.equal(runs.length, 2);
  assert.deepEqual(published, [{ root: "/one", result: "trailing" }]);
});

test("status updates for different roots may run independently", () => {
  const runs = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    () => {}
  );

  coordinator.show("/one");
  coordinator.show("/two");

  assert.deepEqual(runs.map(({ root }) => root), ["/one", "/two"]);
});

test("stale status callbacks cannot publish after the visible root changes", () => {
  const runs = [];
  const published = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    (root, result) => published.push({ root, result })
  );

  coordinator.show("/one");
  coordinator.show("/two");
  runs.find(({ root }) => root === "/one").complete("stale");
  runs.find(({ root }) => root === "/two").complete("current");

  assert.deepEqual(published, [{ root: "/two", result: "current" }]);
});

test("stale status callbacks cannot launch trailing work after the visible root changes", () => {
  const runs = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    () => {}
  );

  coordinator.show("/one");
  coordinator.refresh("/one");
  coordinator.show("/two");
  runs[0].complete("stale");

  assert.deepEqual(runs.map(({ root }) => root), ["/one", "/two"]);
});

test("refresh for a non-visible root cannot start status work", () => {
  const runs = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    () => {}
  );

  coordinator.show("/one");
  runs[0].complete("one");
  coordinator.show("/two");
  coordinator.refresh("/one");

  assert.deepEqual(runs.map(({ root }) => root), ["/one", "/two"]);
});

test("switching away and back still rejects the original stale callback", () => {
  const runs = [];
  const published = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    (root, result) => published.push({ root, result })
  );

  coordinator.show("/one");
  coordinator.show("/two");
  coordinator.show("/one");
  runs[0].complete("original");

  assert.deepEqual(published, []);
  assert.deepEqual(runs.map(({ root }) => root), ["/one", "/two", "/one"]);
  runs[2].complete("latest");
  assert.deepEqual(published, [{ root: "/one", result: "latest" }]);
});

test("clearing status coordination discards trailing work", () => {
  const runs = [];
  const published = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    (root, result) => published.push({ root, result })
  );

  coordinator.show("/one");
  coordinator.refresh("/one");
  coordinator.clear();
  runs[0].complete("stale");

  assert.equal(runs.length, 1);
  assert.deepEqual(published, []);
});

test("show cannot start status work after coordination is cleared", () => {
  const runs = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    () => {}
  );

  coordinator.clear();
  coordinator.show("/one");

  assert.deepEqual(runs, []);
});

test("refresh cannot start status work after coordination is cleared", () => {
  const runs = [];
  const coordinator = new StatusUpdateCoordinator(
    (root, complete) => runs.push({ root, complete }),
    () => {}
  );

  coordinator.clear();
  coordinator.refresh("/one");

  assert.deepEqual(runs, []);
});
