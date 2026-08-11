interface IconProps {
  className?: string;
}

const base = {
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.4,
  strokeLinecap: "round" as const,
};

export function RepoIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <rect x="2.5" y="2.5" width="11" height="11" rx="1" />
      <path d="M2.5 6.2h11" />
    </svg>
  );
}

export function DiffIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <path d="M8 2.8v5M5.5 5.3h5" />
      <path d="M5.5 12.4h5" />
    </svg>
  );
}

export function PrIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <circle cx="4.4" cy="3.9" r="1.7" />
      <circle cx="4.4" cy="12.1" r="1.7" />
      <circle cx="11.6" cy="12.1" r="1.7" />
      <path d="M4.4 5.8v4.4M11.6 10.3V7.6a2 2 0 0 0-2-2H8.2" />
    </svg>
  );
}

export function EnvIcon({ className }: IconProps) {
  return (
    <svg {...base} className={className}>
      <rect x="2" y="3" width="12" height="8.4" rx="1" />
      <path d="M6 13.8h4" />
    </svg>
  );
}

export function TermIcon({ className }: IconProps) {
  return (
    <svg {...base} strokeWidth={1.4} strokeLinejoin="round" className={className}>
      <rect x="2" y="2.6" width="12" height="10.8" rx="1" />
      <path d="M4.8 6.4l2.4 1.9-2.4 1.9M8.8 10.4h2.6" />
    </svg>
  );
}

export function PlusIcon({ className }: IconProps) {
  return (
    <svg {...base} strokeWidth={1.5} className={className}>
      <path d="M8 3.4v9.2M3.4 8h9.2" />
    </svg>
  );
}

export function ChevronIcon({ className }: IconProps) {
  return (
    <svg {...base} strokeWidth={1.5} strokeLinejoin="round" className={className}>
      <path d="M4.8 6.4L8 9.6l3.2-3.2" />
    </svg>
  );
}
