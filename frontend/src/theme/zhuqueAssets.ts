const assetBaseUrl = (import.meta.env.VITE_ZHUQUE_ASSET_BASE_URL || '').replace(/\/$/, '');

const buildAssetUrl = (fileName: string) => (assetBaseUrl ? `${assetBaseUrl}/${fileName}` : '');

export const zhuqueAssetUrls = {
  brandMark: buildAssetUrl('zhuque-brand-mark.webp'),
  emptyState: buildAssetUrl('zhuque-empty-state.webp'),
  loginHero: buildAssetUrl('zhuque-login-hero.webp'),
  paperTexture: buildAssetUrl('zhuque-paper-texture.webp'),
} as const;
