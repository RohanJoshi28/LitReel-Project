import { DEFAULT_STYLE } from './state.js';

export function getSlideStyle(slide) {
  return { ...DEFAULT_STYLE, ...(slide?.style || {}) };
}

export function applySlideStyles(element, style = DEFAULT_STYLE) {
  const merged = { ...DEFAULT_STYLE, ...style };
  const textColor = (merged.text_color || DEFAULT_STYLE.text_color).toUpperCase();
  const outlineColor = (merged.outline_color || DEFAULT_STYLE.outline_color).toUpperCase();
  element.style.setProperty('--slide-text-color', textColor);
  element.style.setProperty('--slide-outline-color', outlineColor);
  const bold = merged.font_weight && merged.font_weight !== '400';
  element.style.fontWeight = bold ? '700' : '400';
  element.style.letterSpacing = bold ? '0.015em' : '0.008em';
  if (merged.underline) {
    element.style.textDecoration = 'underline';
    element.style.textDecorationColor = textColor;
    element.style.textDecorationThickness = '0.15em';
    element.style.textUnderlineOffset = '0.25em';
  } else {
    element.style.textDecoration = 'none';
  }
}

export function applyEffectClass(slideNode, effect = 'none') {
  slideNode.classList.remove('effect-zoom-in', 'effect-zoom-out', 'effect-pan-left', 'effect-pan-right');
  if (effect && effect !== 'none') {
    slideNode.classList.add(`effect-${effect}`);
  }
}
