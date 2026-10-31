---
version: "alpha"
name: "Gradient Aurora Borealis"
description: "Gradient aurora borealis infographic. Ideal for landing pages, modern websites. AI-ready template."
colors:
  primary: "#0A0E27"
  secondary: "#E8F4F8"
  tertiary: "#00D9FF"
  neutral: "#00FF88"
  surface: "#FF006E"
  accent: "#00C9FF"
typography:
  h1:
    fontFamily: System UI stack
    fontSize: 2.25rem
    fontWeight: 700
  body-md:
    fontFamily: System UI stack
    fontSize: 1rem
    fontWeight: 400
  label-caps:
    fontFamily: System UI stack
    fontSize: 0.75rem
    fontWeight: 500
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.neutral}"
    padding: 12px
---
## https://designmd.app/library/gradient-aurora-borealis

## Overview

Gradient aurora borealis infographic. Ideal for landing pages, modern websites. AI-ready template. Aurora gradients in data visualization didn't start with dashboards. They started with NASA. The deep blues bleeding into greens, the magenta arcs dissolving into black — these were colors pulled from satellite imagery and repurposed by designers who understood that scientific data deserves more than bar charts on white backgrounds. The space-tech aesthetic emerged in the early 2010s when agencies and premium brands realized that luminescent color transitions could make complex datasets feel approachable without dumbing them down.

What makes aurora palettes work in infographics is contrast behavior. Dark backgrounds with glowing data points mimic how we actually perceive light phenomena — bright against void. This isn't decoration. It's leveraging how human vision prioritizes luminance differences. When Bloomberg or SpaceX use these gradients in their data presentations, they're not being flashy. They're encoding hierarchy through light intensity.

The premium association is earned, not arbitrary. Luminescent waves signal precision, advanced technology, the kind of care that costs money. A gradient that shifts from deep violet through teal to pale green carries implicit authority — it says this data matters, this organization operates at scale.

- Density: 5/10 — Balanced
- Variance: 4/10 — Moderate
- Motion: 8/10 — Cinematic

- **Style:** Infographic
- **Keywords:** Aurora borealis gradients, cosmic waves, luminescent light waves, magical shimmer, ethereal transparency, dreamy, inspiring, northern lights
- **Era:** Cosmic Ethereal
- **Light/Dark:** ✗ No / ✓ Full

## Colors

- **Deep Space** (#0A0E27) — Primary surface or dominant color
- **Light Cyan Text** (#E8F4F8) — Primary text color
- **Bright Cyan** (#00D9FF) — Accent highlight, links and focus states
- **Mint Green** (#00FF88) — Supporting palette color
- **Hot Pink** (#FF006E) — Primary text color
- **Sky Cyan** (#00C9FF) — Secondary accent
- **Purple** (#7928CA) — Accent color, emphasis elements
- **Teal** (#50E3C2) — Secondary accent


## Typography

- **Display / Hero:** System UI stack (-apple-system, sans-serif) — Weight 700, tight tracking, used for headline impact
- **Body:** System UI stack (-apple-system, sans-serif) — Weight 400, 16px/1.6 line-height, max 72ch per line
- **UI Labels / Captions:** System UI stack (-apple-system, sans-serif) — 0.875rem, weight 500, slight letter-spacing
- **Monospace:** JetBrains Mono — Used for code, metadata, and technical values

Scale:
- Hero: clamp(2.5rem, 5vw, 4rem)
- H1: 2.25rem
- H2: 1.5rem
- Body: 1rem / 1.6
- Small: 0.875rem


## Layout

- **Grid:** CSS Grid primary. Max-width containment: 1280px centered with 1.5rem side padding.
- **Spacing rhythm:** Balanced. Base unit: 0.5rem (8px).
- **Section vertical gaps:** clamp(4rem, 8vw, 8rem).
- **Hero layout:** Split-screen (text left, visual right).
- **Feature sections:** Zig-zag alternating text+image rows. No 3-equal-columns.
- **Mobile collapse:** All multi-column layouts collapse below 768px. No horizontal overflow.
- **z-index contract:** base (0) / sticky-nav (100) / overlay (200) / modal (300) / toast (500).


## Elevation & Depth

Multicolor luminescent lighting, magical glow, aurora wave animations (flowing gradients), cosmic particle effects, ethereal fade transitions, light wave pulses

- **Physics:** Spring — stiffness 120, damping 20. Confident, weighted transitions.
- **Entry animations:** Fade + translate-Y (16px → 0) over 540ms ease-out. Staggered cascades for lists: 120ms between items.
- **Hover states:** Scale(1.03) + shadow lift over 200ms.
- **Page transitions:** Fade + slide (300ms).
- **Performance:** Only transform and opacity animated. No layout-triggering properties.


## Shapes

Base corner radius: 8px. See rounded tokens in front matter for the full scale.


## Components

- **Primary Button:** Subtly rounded (0.5rem) shape. Accent color fill. Hover: 8% darken + subtle lift shadow. Active: -1px translate tactile press. Font weight 600. No outer glows.
- **Secondary / Ghost Button:** Outline variant. 1.5px border in muted color. Text in primary color. Hover: subtle background fill.
- **Cards:** Subtly rounded (0.5rem) corners. Surface background. Subtle shadow (0 2px 12px rgba(0,0,0,0.06)). 1px border stroke.
- **Inputs:** Label above input. 1px border stroke. Focus ring: 2px accent color offset 2px. Error text below in semantic red. No floating labels.
- **Navigation:** Primary surface background. Active item: accent color indicator. Font weight 500 when active.
- **Skeletons:** Shimmer animation matching component dimensions. No circular spinners.
- **Empty States:** Icon-based composition with descriptive text and action button.


## Do's and Don'ts

- No emojis in UI — use icon system only (Lucide, Heroicons)
- No pure black (#000000) — use off-black or charcoal variants
- No oversaturated accent colors (saturation cap: 80%)
- No 3-column equal-width feature layouts — use zig-zag or asymmetric grid
- No `h-screen` — use `min-h-[100dvh]`
- No AI copywriting clichés: "Elevate", "Seamless", "Unleash", "Next-Gen"
- No broken external image links — use picsum.photos or inline SVG
- No generic lorem ipsum in demos

- Do Aurora gradients flowing
- Do Deep space background
- Do Luminescent effects
- Do Cosmic particles
- Do Text readable
- Do Magical ethereal feel


## Use Case

Landing pages, Modern websites
