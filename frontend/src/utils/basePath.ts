const KNOWN_APP_ROOT_SEGMENTS = new Set([
  '',
  'login',
  'auth',
  'projects',
  'project',
  'wizard',
  'inspiration',
  'settings',
  'prompt-templates',
  'mcp-plugins',
  'user-management',
  'chapters',
  'comic-admin',
]);

const ABSOLUTE_URL_PATTERN = /^[a-z][a-z\d+.-]*:\/\//i;

const normalizeBase = (value?: string | null): string => {
  if (!value || value === '/') {
    return '';
  }

  const withLeadingSlash = value.startsWith('/') ? value : `/${value}`;
  return withLeadingSlash.replace(/\/+$/, '');
};

export const getAppBasename = (): string => {
  const envBase = normalizeBase(import.meta.env.BASE_URL);
  if (envBase) {
    return envBase;
  }

  if (typeof window === 'undefined') {
    return '';
  }

  const [firstSegment = ''] = window.location.pathname.split('/').filter(Boolean);
  return KNOWN_APP_ROOT_SEGMENTS.has(firstSegment) ? '' : `/${firstSegment}`;
};

export const buildAppPath = (path = ''): string => {
  if (ABSOLUTE_URL_PATTERN.test(path)) {
    return path;
  }

  const basename = getAppBasename();
  if (basename && path.startsWith(`${basename}/`)) {
    return path;
  }

  const normalizedPath = path ? (path.startsWith('/') ? path : `/${path}`) : '';
  return `${basename}${normalizedPath}` || '/';
};

export const buildApiPath = (path = ''): string => {
  if (ABSOLUTE_URL_PATTERN.test(path)) {
    return path;
  }

  const normalizedPath = path ? (path.startsWith('/') ? path : `/${path}`) : '';
  if (normalizedPath.startsWith('/api/')) {
    return buildAppPath(normalizedPath);
  }
  return `${buildAppPath('/api')}${normalizedPath}`;
};
