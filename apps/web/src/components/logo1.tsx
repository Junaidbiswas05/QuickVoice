export default function Logo1({
  compactOnMobile = false,
}: {
  compactOnMobile?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 text-black dark:text-white">
      {/* Icon */}
      <div className="w-8 h-8">
        <svg suppressHydrationWarning
          width="100%"
          height="100%"
          viewBox="0 0 64 64"
          xmlns="http://www.w3.org/2000/svg"
          shapeRendering="geometricPrecision"
        >
          <g transform="rotate(-12 32 32)">
            <circle
              cx="32"
              cy="32"
              r="28"
              stroke="currentColor"
              strokeWidth="6"
              fill="none"
            />
            <rect
              x="18"
              y="26"
              width="6"
              height="12"
              rx="3"
              fill="currentColor"
            />
            <rect
              x="26"
              y="20"
              width="6"
              height="24"
              rx="3"
              fill="currentColor"
            />
            <rect
              x="34"
              y="20"
              width="6"
              height="24"
              rx="3"
              fill="currentColor"
            />
            <rect
              x="42"
              y="26"
              width="6"
              height="12"
              rx="3"
              fill="currentColor"
            />
          </g>
        </svg>
      </div>

      {/* Text Logo */}
      <svg suppressHydrationWarning
        viewBox="0 0 900 150"
        className={`${compactOnMobile ? "hidden sm:block" : ""} h-8 w-auto`}
        xmlns="http://www.w3.org/2000/svg"
      >
        <text
          x="0"
          y="120"
          fontFamily="Inter, Poppins, Arial, sans-serif"
          fontSize="120"
          fontWeight="800"
          fill="currentColor"
        >
          QuickVoice
        </text>
      </svg>
    </div>
  );
}
