// Run with `npm test` (node --test with TypeScript type stripping).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { ListingOut } from "../api/types.gen.ts";
import {
  LISTINGS_EMPTY_MESSAGE,
  SEARCH_INVALID_MESSAGE,
  ADDED_BY_YOU,
  applyLabel,
  checkSearchQuery,
  listingMeta,
  queryFromParam,
  resultCountMessage,
  searchEmptyMessage,
  searchHref,
  searchTokens,
  NOT_AFFILIATED_NOTICE,
  formatDate,
  formatJobType,
  formatTimestampDate,
  listingDates,
  listingPath,
  mergeListingPages,
  safeExternalUrl,
  sourceAttribution,
  sourceLine,
} from "./listings.ts";

function listing(id: string, overrides: Partial<ListingOut> = {}): ListingOut {
  return {
    id,
    title: "Backend Intern",
    company: "Acme",
    location: "Remote",
    job_type: "internship",
    posted_at: null,
    deadline: null,
    url: "https://www.linkedin.com/jobs/view/1",
    source: "linkedin",
    source_label: "LinkedIn",
    skills: [],
    first_seen_at: "2026-11-14T00:00:00Z",
    ...overrides,
  };
}

test("copy", () => {
  assert.equal(NOT_AFFILIATED_NOTICE, "CareerLaunch is not affiliated with LinkedIn or any job board.");
  assert.equal(LISTINGS_EMPTY_MESSAGE, "No Listings yet — new ones arrive as collection runs.");
  assert.equal(sourceAttribution(listing("a")), "via LinkedIn");
});

test("manual Listings name no Source", () => {
  const manual = listing("m", { source: "manual", source_label: "Manual" });
  assert.equal(sourceAttribution(manual), null);
  assert.equal(sourceLine(manual), ADDED_BY_YOU);
  assert.equal(ADDED_BY_YOU, "Added by you");
  assert.equal(sourceLine(listing("a")), "via LinkedIn");
  assert.equal(applyLabel(manual), "Apply");
  assert.equal(applyLabel(listing("a")), "Apply on LinkedIn");
  assert.equal(
    sourceAttribution(listing("d", { source: "demo", source_label: "Demo data" })),
    "via Demo data",
  );
});

test("meta line skips missing parts", () => {
  assert.equal(listingMeta(listing("a")), "Acme · Remote · Internship");
  assert.equal(listingMeta(listing("a", { company: "", job_type: null })), "Remote");
  assert.equal(listingMeta(listing("a", { company: "  ", location: "", job_type: "full_time" })), "Full-time");
  assert.equal(listingMeta(listing("a", { company: "", location: "", job_type: null })), "");
});

test("listing path encodes the id", () => {
  assert.equal(listingPath("0193-abc"), "/listings/0193-abc");
  assert.equal(listingPath("a/b?c"), "/listings/a%2Fb%3Fc");
});

test("job type labels", () => {
  assert.equal(formatJobType("internship"), "Internship");
  assert.equal(formatJobType("full_time"), "Full-time");
  assert.equal(formatJobType(null), null);
});

test("calendar dates never shift with the time zone", () => {
  assert.equal(formatDate("2026-12-15"), "15 Dec 2026");
  assert.equal(formatDate("2026-01-01"), "1 Jan 2026");
  assert.equal(formatDate(null), null);
  assert.equal(formatDate(""), null);
  assert.equal(formatDate("2026-02-30"), null);
  assert.equal(formatDate("15/12/2026"), null);
});

test("timestamps format as a date in the given zone", () => {
  assert.equal(formatTimestampDate("2026-11-01T09:30:00Z", "en-GB", "UTC"), "1 Nov 2026");
  // 20:00 UTC is already the next day in India.
  assert.equal(formatTimestampDate("2026-11-01T20:00:00Z", "en-GB", "Asia/Kolkata"), "2 Nov 2026");
  assert.equal(formatTimestampDate("not a date"), null);
  assert.equal(formatTimestampDate(null), null);
});

test("listing dates line", () => {
  assert.equal(
    listingDates({ posted_at: "2026-11-01T09:30:00Z", deadline: "2026-12-15" }, "en-GB", "UTC"),
    "Posted 1 Nov 2026 · Apply by 15 Dec 2026",
  );
  assert.equal(listingDates({ posted_at: null, deadline: "2026-12-15" }), "Apply by 15 Dec 2026");
  assert.equal(
    listingDates({ posted_at: "2026-11-01T09:30:00Z", deadline: null }, "en-GB", "UTC"),
    "Posted 1 Nov 2026",
  );
  assert.equal(listingDates({ posted_at: null, deadline: null }), null);
});

test("only http(s) URLs are safe", () => {
  assert.equal(safeExternalUrl("https://www.linkedin.com/jobs/view/1"), "https://www.linkedin.com/jobs/view/1");
  assert.equal(safeExternalUrl("http://example.com/a?b=1"), "http://example.com/a?b=1");
  assert.equal(safeExternalUrl("HTTPS://Example.com/x"), "https://example.com/x");
  for (const bad of [
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    " javascript:alert(1)",
    "java\nscript:alert(1)",
    "data:text/html,<b>x</b>",
    "vbscript:msgbox(1)",
    "ftp://example.com/x",
    "//example.com/x",
    "/jobs/1",
    "https://exa mple.com",
    "https:\\\\evil.com",
    "",
    null,
    undefined,
  ]) {
    assert.equal(safeExternalUrl(bad), null, String(bad));
  }
});

test("merge pages without duplicates", () => {
  const merged = mergeListingPages([listing("a"), listing("b")], [listing("b"), listing("c")]);
  assert.deepEqual(
    merged.map((l) => l.id),
    ["a", "b", "c"],
  );
});

test("search tokens mirror the API rules", () => {
  assert.deepEqual(searchTokens("  Backend,  INTERN!! "), ["backend", "intern"]);
  assert.deepEqual(searchTokens("ＢＡＣＫＥＮＤ"), ["backend"]);
  assert.deepEqual(searchTokens("a:* & !b | c"), ["a", "b", "c"]);
  assert.deepEqual(searchTokens("naïve café 数据"), ["naïve", "café", "数据"]);
  assert.deepEqual(searchTokens("'); drop table listings; --"), ["drop", "table", "listings"]);
  assert.deepEqual(
    searchTokens(Array.from({ length: 20 }, (_, n) => `word${n}`).join(" ")),
    Array.from({ length: 8 }, (_, n) => `word${n}`),
  );
  assert.deepEqual(searchTokens("y".repeat(90)), ["y".repeat(40)]);
  assert.deepEqual(searchTokens(""), []);
});

test("checkSearchQuery accepts usable queries and trims them", () => {
  assert.deepEqual(checkSearchQuery("  bakend intrn "), { ok: true, query: "bakend intrn" });
  assert.deepEqual(checkSearchQuery("c#"), { ok: false, message: SEARCH_INVALID_MESSAGE });
  assert.deepEqual(checkSearchQuery("go"), { ok: true, query: "go" });
  assert.deepEqual(checkSearchQuery("x".repeat(100)), { ok: true, query: "x".repeat(100) });
});

test("checkSearchQuery rejects what the API rejects", () => {
  for (const bad of ["", "   ", "a", " a ", "!!!", "x".repeat(101), null, undefined]) {
    assert.deepEqual(checkSearchQuery(bad), { ok: false, message: SEARCH_INVALID_MESSAGE }, String(bad));
  }
});

test("queryFromParam reads the q parameter", () => {
  assert.equal(queryFromParam(undefined), "");
  assert.equal(queryFromParam(null), "");
  assert.equal(queryFromParam("  intern "), "intern");
  assert.equal(queryFromParam(["first", "second"]), "first");
  assert.equal(queryFromParam([]), "");
});

test("searchHref builds a shareable URL and falls back to browse", () => {
  assert.equal(searchHref("backend intern"), "/listings?q=backend%20intern");
  assert.equal(searchHref("c++ & <b>"), "/listings?q=c%2B%2B%20%26%20%3Cb%3E");
  assert.equal(searchHref("   "), "/listings");
  assert.equal(searchHref(""), "/listings");
});

test("the empty search message is neutral and echoes the query as text", () => {
  assert.equal(
    searchEmptyMessage("zzzz"),
    "No Listings match “zzzz”. Try fewer or broader terms, or check the spelling.",
  );
  assert.match(searchEmptyMessage("<img src=x>"), /<img src=x>/);
});

test("the result count message is announced with the paging state", () => {
  assert.equal(resultCountMessage(1, false), "1 Listing shown.");
  assert.equal(resultCountMessage(20, true), "20 Listings shown, more available.");
});
