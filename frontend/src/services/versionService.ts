import { VERSION_INFO } from '../config/version';

interface VersionCheckResult {
  hasUpdate: boolean;
  latestVersion: string;
  releaseUrl: string;
}

/** 版本检查已禁用，避免前端运行时请求外部服务。 */
export async function checkLatestVersion(): Promise<VersionCheckResult> {
  return {
    hasUpdate: false,
    latestVersion: VERSION_INFO.version,
    releaseUrl: VERSION_INFO.githubUrl,
  };
}

/**
 * 检查是否应该执行版本检查（避免频繁请求）
 */
export function shouldCheckVersion(): boolean {
  return false;
}

/**
 * 记录版本检查时间
 */
export function markVersionChecked(): void {
  localStorage.setItem('version_last_check', new Date().toISOString());
}

/**
 * 获取缓存的版本信息
 */
export function getCachedVersionInfo(): VersionCheckResult | null {
  const cached = localStorage.getItem('version_check_result');
  if (cached) {
    try {
      return JSON.parse(cached);
    } catch {
      return null;
    }
  }
  return null;
}

/**
 * 缓存版本信息
 */
export function cacheVersionInfo(info: VersionCheckResult): void {
  localStorage.setItem('version_check_result', JSON.stringify(info));
}

/**
 * 用户已查看更新提示
 */
export function markUpdateViewed(version: string): void {
  localStorage.setItem('version_viewed', version);
}

/**
 * 检查用户是否已查看此版本的更新提示
 */
export function hasViewedUpdate(version: string): boolean {
  const viewedVersion = localStorage.getItem('version_viewed');
  
  // 如果已查看的版本低于最新版本，应该显示红点
  if (viewedVersion && version) {
    const parts1 = viewedVersion.split('.').map(Number);
    const parts2 = version.split('.').map(Number);
    
    for (let i = 0; i < Math.max(parts1.length, parts2.length); i++) {
      const num1 = parts1[i] || 0;
      const num2 = parts2[i] || 0;
      
      if (num1 < num2) {
        return false; // 已查看的版本低于最新版本，需要显示红点
      }
      if (num1 > num2) {
        return true; // 已查看的版本高于最新版本
      }
    }
  }
  
  return viewedVersion === version;
}
