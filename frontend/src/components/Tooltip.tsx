/** Tooltip wrapper with viewport-aware positioning via a portal.
 *  Renders the tip into document.body so ancestor transforms don't break fixed positioning. */

import { useRef, useState, useCallback, type ReactNode, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import styles from './Tooltip.module.css';

interface Props {
  text: ReactNode;
  children: ReactNode;
  position?: 'top' | 'bottom' | 'left' | 'right';
  style?: CSSProperties;
  disabled?: boolean;
}

const GAP = 6;
const EDGE_PAD = 6;

export function Tooltip({ text, children, position = 'top', style, disabled }: Props) {
  const tipRef = useRef<HTMLSpanElement>(null);
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const [visible, setVisible] = useState(false);

  const reposition = useCallback(() => {
    const tip = tipRef.current;
    const wrapper = wrapperRef.current;
    if (!tip || !wrapper) return;

    const anchor = wrapper.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    const tw = tipRect.width;
    const th = tipRect.height;

    let top = 0;
    let left = 0;

    if (position === 'top') {
      top = anchor.top - th - GAP;
      left = anchor.left + anchor.width / 2 - tw / 2;
    } else if (position === 'bottom') {
      top = anchor.bottom + GAP;
      left = anchor.left + anchor.width / 2 - tw / 2;
    } else if (position === 'left') {
      top = anchor.top + anchor.height / 2 - th / 2;
      left = anchor.left - tw - GAP;
    } else {
      top = anchor.top + anchor.height / 2 - th / 2;
      left = anchor.right + GAP;
    }

    // Clamp to viewport
    if (left < EDGE_PAD) left = EDGE_PAD;
    else if (left + tw > window.innerWidth - EDGE_PAD)
      left = window.innerWidth - EDGE_PAD - tw;

    if (top < EDGE_PAD) top = EDGE_PAD;
    else if (top + th > window.innerHeight - EDGE_PAD)
      top = window.innerHeight - EDGE_PAD - th;

    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;
  }, [position]);

  const handleEnter = useCallback(() => {
    setVisible(true);
    // Reposition after the portal renders
    requestAnimationFrame(() => reposition());
  }, [reposition]);

  const handleLeave = useCallback(() => {
    setVisible(false);
  }, []);

  return (
    <span
      ref={wrapperRef}
      className={styles.wrapper}
      style={style}
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
    >
      {children}
      {!disabled && visible && createPortal(
        <span ref={tipRef} className={`${styles.tip} ${styles.tipVisible}`}>
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}
