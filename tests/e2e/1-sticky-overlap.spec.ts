import { test, expect } from "@playwright/test";

/**
 * Layout regression: when the user scrolls past the natural top of
 * `#tracks-sticky`, the sticky filter bar pins to the viewport. The
 * virtualized cards inside `#track-list` then need to flow under the
 * sticky bar cleanly — the first card visible below the sticky must
 * be FULLY visible, not clipped with only its bottom slice (cue-warning
 * row) showing.
 *
 * The original bug: `#track-list` only reserved `padding-top: 12px`,
 * but the sticky bar's height when pinned is ~238px. Cards positioned by
 * the Virtualizer started at `#track-list.top + 0px`, so the first card's
 * top was hidden under the sticky bar while its bottom (warning badges)
 * leaked out below — an "orphan ⚠ row" floating between sticky and the
 * next visible card.
 *
 * This kind of regression is invisible to the JSDOM Vitest layer because
 * JSDOM does not compute layout (every `getBoundingClientRect()` returns
 * zeros). It needs a real browser. This spec is the regression guard.
 *
 * Named with a leading `1-` so it runs right after `0-safety.spec.ts` and
 * fails fast if the layout invariant is broken.
 */
test.describe("Sticky filter bar — virtualized card overlap (regression)", () => {
  test("first visible card's top is at or below sticky bar's bottom when scrolled", async ({
    page,
  }) => {
    await page.goto("/");

    // Wait for the local-mode UI to settle and at least one card to render.
    // The Virtualizer renders cards asynchronously after /api/tracks
    // resolves, so a simple selector wait isn't enough — wait until the
    // card pool has been populated with non-zero-height nodes.
    await expect(page.locator("#tracks-sticky")).toBeVisible({ timeout: 10_000 });
    await expect(page.locator("#track-list")).toBeVisible({ timeout: 10_000 });

    // Default sort is "album" (album-group layout — every card in DOM,
    // grouped by album). The regression is on the flat virtualized list
    // (any non-album sort), where the Virtualizer absolute-positions
    // cards inside `#track-list` and the sticky bar overlaps the first
    // card. Switch to Title sort to reproduce. In the workbench the sort
    // lives in the grid-head columns (the legacy #sort-bar is folded into
    // the Filters panel), so click the Title column — it delegates to the
    // same sort handler.
    await page.locator('#wb-grid-head .wb-c-title.wb-sortable').click();
    await page.waitForFunction(
      () => {
        const list = document.getElementById("track-list");
        return !!list && list.classList.contains("virtualized");
      },
      undefined,
      { timeout: 10_000 },
    );
    // Diagnostic: capture the DOM state before the wait, so a timeout
    // here surfaces useful information rather than a bare TimeoutError.
    try {
      await page.waitForFunction(
        () => {
          const list = document.getElementById("track-list");
          if (!list) return false;
          const cards = list.querySelectorAll<HTMLElement>(".track-card");
          return (
            cards.length > 0 &&
            Array.from(cards).some(
              (c) => c.getBoundingClientRect().height > 0,
            )
          );
        },
        undefined,
        { timeout: 20_000 },
      );
    } catch (e) {
      const diag = await page.evaluate(() => {
        const list = document.getElementById("track-list");
        return {
          listExists: !!list,
          listClasses: list?.className ?? null,
          listChildCount: list?.children.length ?? 0,
          listInnerHTMLPreview:
            list?.innerHTML?.slice(0, 400) ?? null,
          trackCardCount: document.querySelectorAll(".track-card").length,
          dataTrackIdCount:
            document.querySelectorAll("[data-track-id]").length,
          appStatusText:
            document.getElementById("app-status")?.textContent ?? null,
          warmupChip:
            document.getElementById("status-warmup")?.textContent ?? null,
        };
      });
      throw new Error(
        `track-card wait timed out — diagnostic: ${JSON.stringify(diag, null, 2)}`,
      );
    }

    // The Title-sort re-render fires a FLIP animation (250ms) on cards
    // that survived the re-attach. Wait it out so the cards' inline
    // transforms reflect the snap state, not an animated intermediate.
    await page.waitForTimeout(350);

    // Scroll past the natural top of #tracks-sticky so it pins to the
    // viewport. 320px is more than enough — the sticky's natural top sits
    // below the page header at ~y=114, so scrolling 320 engages stickiness
    // and pulls a card into the overlap zone.
    await page.evaluate(() => window.scrollTo(0, 320));
    // Wait until the snap has stabilized — the first visible card's
    // bottom should align with the sticky's bottom (within 1px). This
    // also covers any straggler RAF/repaint after the scroll event.
    // Tolerance for picking which card counts as "first visible" — must be
    // bigger than sub-pixel rounding noise but smaller than any half-card
    // we'd want to catch. The Virtualizer's snap targets card.bottom ≈
    // stickyBottom for the LAST hidden card; that comparison can drift by
    // ~0.5 px between the snap-compute frame and the probe frame (issue
    // #187 — picking card[0] for a 0.44 px overshoot reported a 159 px
    // false-positive overlap when the user-visible behaviour was correct).
    // 5 px keeps the original "≥5 px overlap = bug" assertion intact.
    const VISIBLE_THRESHOLD_PX = 5;

    await page.waitForFunction(
      (threshold) => {
        const sticky = document.getElementById("tracks-sticky");
        const list = document.getElementById("track-list");
        if (!sticky || !list) return false;
        const sb = sticky.getBoundingClientRect().bottom;
        const cards = Array.from(
          list.querySelectorAll<HTMLElement>(".track-card"),
        );
        const visible = cards.find(
          (c) => c.getBoundingClientRect().bottom > sb + threshold,
        );
        if (!visible) return false;
        return visible.getBoundingClientRect().top >= sb - 1;
      },
      VISIBLE_THRESHOLD_PX,
      { timeout: 5_000 },
    ).catch(() => {
      // Fall through — let the assertion below produce the diagnostic.
    });

    const layout = await page.evaluate((threshold) => {
      const sticky = document.getElementById("tracks-sticky")!;
      const list = document.getElementById("track-list")!;
      const cards = Array.from(
        list.querySelectorAll<HTMLElement>(".track-card"),
      ).sort(
        (a, b) =>
          a.getBoundingClientRect().top - b.getBoundingClientRect().top,
      );
      const stickyRect = sticky.getBoundingClientRect();
      const listRect = list.getBoundingClientRect();
      const stickyBottom = stickyRect.bottom;
      // First card whose bottom edge is *materially* below the sticky.
      // The +threshold filters out the "last hidden card whose bottom
      // happens to overshoot stickyBottom by sub-pixel rounding" case
      // (issue #187). Anything past the threshold has enough visible
      // content that an actual overlap would be obvious to the user.
      const firstVisible = cards.find(
        (c) => c.getBoundingClientRect().bottom > stickyBottom + threshold,
      );
      const firstVisibleRect = firstVisible?.getBoundingClientRect();
      const firstCardTransform = cards[0]?.style.transform ?? null;
      const listPaddingTop = parseFloat(
        getComputedStyle(list).paddingTop,
      );
      return {
        stickyTop: stickyRect.top,
        stickyBottom,
        listTop: listRect.top,
        listPaddingTop,
        firstCardTransform,
        firstVisibleTop: firstVisibleRect?.top ?? null,
        firstVisibleBottom: firstVisibleRect?.bottom ?? null,
        firstVisibleId: firstVisible?.getAttribute("data-track-id") ?? null,
        cardCount: cards.length,
        cardRects: cards.slice(0, 5).map((c) => {
          const r = c.getBoundingClientRect();
          return {
            id: c.getAttribute("data-track-id"),
            top: r.top,
            bottom: r.bottom,
          };
        }),
      };
    }, VISIBLE_THRESHOLD_PX);

    expect(
      layout.firstVisibleTop,
      `expected at least one rendered .track-card whose bottom is below the sticky bar; layout=${JSON.stringify(layout)}`,
    ).not.toBeNull();

    // The first visible card's top edge must be at or very near the sticky
    // bottom — i.e. the card is essentially fully visible, not clipped
    // behind the sticky. The regression was a ~50px overlap (the bottom of
    // a card poking out below the sticky as an orphan ⚠ row). A 5px slack
    // absorbs sub-pixel rounding without losing the regression-catch
    // (anything past 5px would be a visible half-card again).
    //
    // Note: we compare card.BOTTOM against sticky.bottom rather than card
    // top, because the snap aligns the first FULLY-COVERED card's bottom
    // with sticky bottom (i.e., the card just above the visible region).
    // The "first visible" card is the next one — its top should be at or
    // slightly past the sticky bottom.
    const overlap = layout.stickyBottom - layout.firstVisibleTop!;
    expect(
      overlap,
      `first visible card #${layout.firstVisibleId} overlaps sticky by ${overlap}px (top=${layout.firstVisibleTop}, sticky.bottom=${layout.stickyBottom}). The regression was ~50px; anything > 5px is a regression. layout=${JSON.stringify(layout)}`,
    ).toBeLessThanOrEqual(5);
  });
});
