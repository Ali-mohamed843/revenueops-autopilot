"use client";

import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useRef } from "react";

/**
 * Opening a new page starts at its top. Without this, clicking a row low on a long page could land
 * mid-way down the next one: the short loading skeleton clamps the scroll position, then the real
 * page streams in and the browser's scroll anchoring pushes the view down. Back and forward keep the
 * browser's own restoration, so returning to a list puts you where you were.
 */
export function ScrollToTop() {
  const pathname = usePathname();
  const historyNavigation = useRef(false);
  const first = useRef(true);

  useEffect(() => {
    const onPopState = () => {
      historyNavigation.current = true;
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useLayoutEffect(() => {
    if (first.current) {
      first.current = false; // the first load is the browser's to place (e.g. a #anchor)
      return;
    }
    if (historyNavigation.current) {
      historyNavigation.current = false;
      return;
    }
    if (!window.location.hash) window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  }, [pathname]);

  return null;
}
