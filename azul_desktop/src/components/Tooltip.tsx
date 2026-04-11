import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface TooltipProps {
  text: string;
  children: React.ReactNode;
  className?: string;
}

export function Tooltip({ text, children, className }: TooltipProps) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  function show() {
    if (!ref.current) return;
    const el = ref.current;
    if (el.scrollWidth <= el.clientWidth) return;
    const rect = el.getBoundingClientRect();
    setPos({ top: rect.top + rect.height / 2, left: rect.right + 10 });
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
            style={{ top: pos.top, left: pos.left }}
          >
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}
