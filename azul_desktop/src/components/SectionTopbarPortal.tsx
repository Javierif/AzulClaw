import type { ReactNode } from "react";
import { createPortal } from "react-dom";

export function SectionTopbarPortal({
  target,
  children,
  fallback = null,
}: {
  target: HTMLElement | null;
  children: ReactNode;
  fallback?: ReactNode;
}) {
  if (!target) {
    return <>{fallback}</>;
  }
  return createPortal(children, target);
}
