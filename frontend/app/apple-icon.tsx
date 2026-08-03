import { ImageResponse } from 'next/og';

// #228 prerequisite. iOS uses the apple-touch-icon for the home-screen icon and
// ignores the manifest's icons array entirely. It also will not accept an SVG
// there -- given one, it falls back to a screenshot of the page -- so the
// existing app/icon.svg cannot serve this role and a raster is required.
//
// Generated at build time from the same mark as icon.svg rather than committing
// a binary, so the two cannot drift apart silently.

export const size = { width: 180, height: 180 };
export const contentType = 'image/png';

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: '100%',
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          // No rounded corners: iOS applies its own mask, and a pre-rounded
          // source would show a double-corner artefact.
          background: '#2563EB',
          color: 'white',
          fontSize: 80,
          fontWeight: 700,
          letterSpacing: '-0.03em',
        }}
      >
        AI
      </div>
    ),
    size,
  );
}
