import { describe, expect, it } from "vitest";

import {
  validateCompareSearch,
  validateDocumentSearch,
  validateSearchSearch,
  validateVersionSearch,
} from "./search";

describe("search validators", () => {
  it("parses numbers from numbers or strings and drops invalid ones", () => {
    expect(validateCompareSearch({ a: 3, b: "4", doc: "d1" })).toEqual({ a: 3, b: 4, doc: "d1" });
    expect(validateCompareSearch({ a: "x", b: 1.5 })).toEqual({});
    expect(validateDocumentSearch({ node: "parse", version: "7", page: 2 })).toEqual({
      node: "parse",
      version: 7,
      page: 2,
    });
  });

  it("keeps numeric-looking strings as strings", () => {
    expect(validateSearchSearch({ q: 2292, k: "20" })).toEqual({ q: "2292", k: 20 });
  });

  it("restricts enumerations", () => {
    expect(validateVersionSearch({ tab: "issues", status: "error", level: "warning", q: "" })).toEqual({
      tab: "issues",
      status: "error",
      level: "warning",
    });
    expect(validateVersionSearch({ tab: "logs", status: "bad", level: "info" })).toEqual({});
  });
});
