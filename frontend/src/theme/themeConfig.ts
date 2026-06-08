import type { ThemeConfig } from 'antd';
import { theme } from 'antd';
import type { ThemeMode } from './themeStorage';
import { zhuqueColors, zhuqueFontFamily } from './zhuqueTokens';

export type ResolvedThemeMode = Exclude<ThemeMode, 'system'>;

const sharedToken: ThemeConfig['token'] = {
  colorPrimary: zhuqueColors.cinnabar,
  borderRadius: 8,
  wireframe: false,
  fontFamily: zhuqueFontFamily,
};

const sharedComponents: ThemeConfig['components'] = {
  Button: {
    borderRadius: 8,
    controlHeight: 36,
  },
  Card: {
    borderRadiusLG: 12,
  },
  Tooltip: {
    colorBgSpotlight: sharedToken.colorPrimary,
  },
};

const lightThemeConfig: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    ...sharedToken,
    colorBgBase: zhuqueColors.paper,
    colorTextBase: zhuqueColors.ink,
    colorBgLayout: zhuqueColors.paper,
    colorBgContainer: zhuqueColors.paperSoft,
  },
  components: {
    ...sharedComponents,
    Layout: {
      bodyBg: zhuqueColors.paper,
      headerBg: zhuqueColors.paperSoft,
      siderBg: zhuqueColors.paperSoft,
    },
  },
};

const darkThemeConfig: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    ...sharedToken,
    colorBgBase: '#141414',
    colorTextBase: '#f5f5f5',
  },
  components: {
    ...sharedComponents,
    Layout: {
      bodyBg: '#0f1115',
      headerBg: '#141414',
      siderBg: '#141414',
    },
  },
};

export const getThemeConfig = (mode: ResolvedThemeMode): ThemeConfig => {
  return mode === 'dark' ? darkThemeConfig : lightThemeConfig;
};
