export const DEFAULT_COMIC_STYLE = 'guoman_refined';

export const COMIC_STYLE_OPTIONS = [
  {
    value: 'guoman_refined',
    label: '精修国漫',
    description: '干净线稿、精修上色、电影感光影，适合玄幻、都市、仙侠等商业漫画。',
  },
  {
    value: 'guoman_ink',
    label: '水墨国风',
    description: '水墨笔触、克制配色、国风意境，适合武侠、仙侠、古风题材。',
  },
  {
    value: 'japanese_cel',
    label: '日漫赛璐璐',
    description: '清晰描线、赛璐璐阴影、表情鲜明，适合轻小说和少年漫画感作品。',
  },
  {
    value: 'korean_webtoon',
    label: '韩漫条漫',
    description: '柔和渐变、时尚人物、条漫质感，适合都市、恋爱、现代奇幻。',
  },
  {
    value: 'dark_fantasy',
    label: '暗黑奇幻',
    description: '强明暗、厚重氛围、华丽服饰，适合悬疑、反派、黑暗幻想。',
  },
  {
    value: 'american_comic',
    label: '美漫厚涂',
    description: '粗线条、动态构图、厚涂质感，适合动作、科幻、超级英雄感题材。',
  },
  {
    value: 'photoreal_cinematic',
    label: '真人写实',
    description: '真实人物比例、现实服装材质、真实场景与电影光影，适合想做真人剧照质感的小说视觉化。',
  },
] as const;

export type ComicStyleValue = typeof COMIC_STYLE_OPTIONS[number]['value'];

export const getComicStyleLabel = (value?: string | null) => {
  return COMIC_STYLE_OPTIONS.find(option => option.value === value)?.label || '精修国漫';
};
