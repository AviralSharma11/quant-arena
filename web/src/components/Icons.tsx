/**
 * The handful of line icons the three screens use, as inline SVG.
 *
 * Drawn here rather than loaded from an icon font: a font is a dependency outside the closed stack
 * list, and until it loads every icon renders as its ligature name ("expand_more") in the middle
 * of a label. Each icon inherits `currentColor`, so it takes the colour of the text beside it.
 */

import type { ReactNode } from "react";

interface IconProps {
  size?: number;
  className?: string;
}

function icon(paths: ReactNode) {
  return function Icon({ size = 16, className }: IconProps) {
    return (
      <svg
        className={className}
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        focusable="false"
      >
        {paths}
      </svg>
    );
  };
}

export const LogoIcon = icon(
  <>
    <polyline points="3 17 9 11 13 15 21 7" />
    <polyline points="15 7 21 7 21 13" />
  </>,
);

export const UserIcon = icon(
  <>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" />
  </>,
);

export const UserPlusIcon = icon(
  <>
    <circle cx="9" cy="8" r="4" />
    <path d="M2 21c0-4 3-6 7-6s7 2 7 6" />
    <path d="M19 8v6M16 11h6" />
  </>,
);

export const LockIcon = icon(
  <>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V7a4 4 0 0 1 8 0v4" />
  </>,
);

export const KeyIcon = icon(
  <>
    <circle cx="7.5" cy="15.5" r="4.5" />
    <path d="M10.7 12.3 21 2M16 7l3 3M14 9l2 2" />
  </>,
);

export const LogInIcon = icon(
  <>
    <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
    <polyline points="10 17 15 12 10 7" />
    <line x1="15" y1="12" x2="3" y2="12" />
  </>,
);

export const LogOutIcon = icon(
  <>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
    <polyline points="16 17 21 12 16 7" />
    <line x1="21" y1="12" x2="9" y2="12" />
  </>,
);

export const WarningIcon = icon(
  <>
    <path d="M12 3 2 21h20L12 3z" />
    <path d="M12 10v5M12 18h.01" />
  </>,
);

export const InfoIcon = icon(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v6M12 7.5h.01" />
  </>,
);

export const PlayIcon = icon(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M10 8.5v7l6-3.5-6-3.5z" fill="currentColor" />
  </>,
);

export const ShieldIcon = icon(
  <>
    <path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3z" />
    <path d="m9 12 2 2 4-4" />
  </>,
);

export const TerminalIcon = icon(
  <>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="m7 9 3 3-3 3M13 15h4" />
  </>,
);

export const SlidersIcon = icon(
  <>
    <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12" />
    <circle cx="16" cy="6" r="2" />
    <circle cx="10" cy="12" r="2" />
    <circle cx="18" cy="18" r="2" />
  </>,
);

export const BarsIcon = icon(<path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />);
