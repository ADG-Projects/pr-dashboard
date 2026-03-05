/** CSS-only tooltip wrapper. Wrap any element to show a tooltip on hover. */

import type { ReactNode, CSSProperties } from 'react';
import styles from './Tooltip.module.css';

interface Props {
  text: string;
  children: ReactNode;
  position?: 'top' | 'bottom' | 'left' | 'right';
  style?: CSSProperties;
}

export function Tooltip({ text, children, position = 'top', style }: Props) {
  return (
    <span className={styles.wrapper} style={style} data-tooltip={text} data-position={position}>
      {children}
    </span>
  );
}
