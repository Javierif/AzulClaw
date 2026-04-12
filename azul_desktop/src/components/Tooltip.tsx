import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

type TooltipPosition = "right" | "left" | "top" | "bottom";

interface TooltipProps {
  text: string;
  children: React.ReactNode;
  className?: string;
  position?: TooltipPosition;
}

function getTooltipPos(rect: DOMRect, position: TooltipPosition) {
  switch (position) {
    case "left":
      return { top: rect.top + rect.height / 2, left: rect.left - 10 };
    case "top":
      return { top: rect.top - 10, left: rect.left + rect.width / 2 };
    case "bottom":
      return { top: rect.bottom + 10, left: rect.left + rect.width / 2 };
    case "right":
    default:
      return { top: rect.top + rect.height / 2, left: rect.right + 10 };
  }
}

function getTooltipTransform(position: TooltipPosition) {
  switch (position) {
    case "left":
      return "translate(-100%, -50%)";
    case "top":
      return "translate(-50%, -100%)";
    case "bottom":
      return "translate(-50%, 0)";
    case "right":
    default:
      return "translateY(-50%)";
  }
}

export function Tooltip({ text, children, className, position = "right" }: TooltipProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  function show() {
    if (!ref.current) return;
    const el = ref.current;
    if (el.scrollWidth <= el.clientWidth) return;
    const rect = el.getBoundingClientRect();
    setPos(getTooltipPos(rect, position));
  }

  function hide() {
    setPos(null);
  }

  useEffect(() => {
    return () => setPos(null);
  }, []);

  return (
    <>
      <span ref={ref} className={className} onMouseEnter={show} onMouseLeave={hide}>
        {children}
      </span>
      {pos &&
        createPortal(
          <div
            className="tooltip-portal"
            style={{ top: pos.top, left: pos.left, transform: getTooltipTransform(position) }}
          >
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}
