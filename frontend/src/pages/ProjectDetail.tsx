import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate, Outlet, Link, useLocation } from 'react-router-dom';
import { Alert, Button, Drawer, Layout, Menu, Progress, Space, Spin, Tag, theme, message } from 'antd';
import {
  ArrowLeftOutlined,
  FileTextOutlined,
  TeamOutlined,
  BookOutlined,
  PictureOutlined,
  // ToolOutlined,
  GlobalOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  ApartmentOutlined,
  BankOutlined,
  EditOutlined,
  FundOutlined,
  TrophyOutlined,
  BulbOutlined,
  CloudOutlined,
  MoonOutlined,
  BgColorsOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { useStore } from '../store';
import { useCharacterSync, useOutlineSync, useChapterSync } from '../store/hooks';
import { bookImportApi, projectApi } from '../services/api';
import type { BookImportFollowupStatus } from '../types';
import ThemeSwitch from '../components/ThemeSwitch';
import ProjectTaskDrawer from '../components/ProjectTaskDrawer';
import { useThemeMode } from '../theme/useThemeMode';
import { getStoredSidebarCollapsed, setStoredSidebarCollapsed } from '../utils/sidebarState';
import { ZHUQUE_BRAND_NAME, zhuqueColors } from '../theme/zhuqueTokens';
import { zhuqueAssetUrls } from '../theme/zhuqueAssets';

const { Header, Sider, Content } = Layout;
const APP_NAME = ZHUQUE_BRAND_NAME;

// 判断是否为移动端
const isMobile = () => window.innerWidth <= 768;

export default function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState<boolean>(() => getStoredSidebarCollapsed());
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [mobile, setMobile] = useState(isMobile());
  const [followupStatus, setFollowupStatus] = useState<BookImportFollowupStatus | null>(null);
  const [followupLoading, setFollowupLoading] = useState(false);
  const [followupStarting, setFollowupStarting] = useState(false);
  const { token } = theme.useToken();
  const alphaColor = (color: string, alpha: number) => `color-mix(in srgb, ${color} ${(alpha * 100).toFixed(0)}%, transparent)`;
  const cinnabar = zhuqueColors.cinnabar;
  const ink = zhuqueColors.ink;
  const teal = zhuqueColors.teal;
  const paper = zhuqueColors.paper;
  const gold = zhuqueColors.gold;
  const { mode, resolvedMode, setMode } = useThemeMode();
  const cycleThemeMode = () => {
    const nextMode = mode === 'light' ? 'dark' : mode === 'dark' ? 'system' : 'light';
    setMode(nextMode);
  };
  const collapsedThemeIcon = mode === 'light' ? <BulbOutlined /> : mode === 'dark' ? <MoonOutlined /> : <CloudOutlined />;

  // 监听窗口大小变化
  useEffect(() => {
    const handleResize = () => {
      setMobile(isMobile());
      if (!isMobile()) {
        setDrawerVisible(false);
      }
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    setStoredSidebarCollapsed(collapsed);
  }, [collapsed]);
  const {
    currentProject,
    setCurrentProject,
    clearProjectData,
    loading,
    setLoading,
    outlines,
    characters,
    chapters,
  } = useStore();

  // 使用同步 hooks
  const { refreshCharacters } = useCharacterSync();
  const { refreshOutlines } = useOutlineSync();
  const { refreshChapters } = useChapterSync();

  const loadFollowupStatus = useCallback(async (options?: { silent?: boolean }) => {
    if (!projectId) return;
    try {
      setFollowupLoading(true);
      const status = await bookImportApi.getFollowupStatus(projectId, { silent: options?.silent ?? true });
      setFollowupStatus(status);
    } catch (error) {
      if (!options?.silent) {
        console.error('加载项目补全状态失败:', error);
      }
    } finally {
      setFollowupLoading(false);
    }
  }, [projectId]);

  const resumeProjectFollowup = useCallback(async () => {
    if (!projectId) return;
    try {
      setFollowupStarting(true);
      const status = await bookImportApi.resumeFollowup(projectId);
      setFollowupStatus(status);
      message.success(status.message || '项目补全任务已启动');
      await Promise.all([
        refreshCharacters(projectId, { silent: true }),
        loadFollowupStatus({ silent: true }),
      ]);
    } catch (error) {
      console.error('启动项目补全失败:', error);
      message.error('启动项目补全失败');
    } finally {
      setFollowupStarting(false);
    }
  }, [projectId, refreshCharacters, loadFollowupStatus]);

  useEffect(() => {
    const loadProjectData = async (id: string) => {
      try {
        setLoading(true);
        // 加载项目基本信息
        const project = await projectApi.getProject(id);
        setCurrentProject(project);

        // 并行加载其他数据
        await Promise.all([
          refreshOutlines(id),
          refreshCharacters(id),
          refreshChapters(id),
        ]);
      } catch (error) {
        console.error('加载项目数据失败:', error);
      } finally {
        setLoading(false);
      }
    };

    if (projectId) {
      loadProjectData(projectId);
      void loadFollowupStatus({ silent: true });
    }

    return () => {
      clearProjectData();
    };
  }, [projectId, clearProjectData, setLoading, setCurrentProject, refreshOutlines, refreshCharacters, refreshChapters, loadFollowupStatus]);

  useEffect(() => {
    if (!projectId || !followupStatus) return;
    const analysisRunning = followupStatus.analysis_tasks.running > 0 || followupStatus.analysis_tasks.pending > 0;
    if (!followupStatus.followup_running && !analysisRunning) return;
    const timer = window.setInterval(() => {
      void loadFollowupStatus({ silent: true });
    }, 10000);
    return () => window.clearInterval(timer);
  }, [projectId, followupStatus, loadFollowupStatus]);

  // 移除事件监听，避免无限循环
  // Hook 内部已经更新了 store，不需要再次刷新

  const menuItems = [
    {
      type: 'group' as const,
      label: '创作管理',
      children: [
        {
          key: 'world-setting',
          icon: <GlobalOutlined />,
          label: <Link to={`/project/${projectId}/world-setting`}>世界设定</Link>,
        },
        {
          key: 'characters',
          icon: <TeamOutlined />,
          label: <Link to={`/project/${projectId}/characters`}>角色管理</Link>,
        },
        {
          key: 'organizations',
          icon: <BankOutlined />,
          label: <Link to={`/project/${projectId}/organizations`}>组织管理</Link>,
        },
        {
          key: 'careers',
          icon: <TrophyOutlined />,
          label: <Link to={`/project/${projectId}/careers`}>职业管理</Link>,
        },
        {
          key: 'relationships',
          icon: <ApartmentOutlined />,
          label: <Link to={`/project/${projectId}/relationships`}>关系管理</Link>,
        },
        {
          key: 'outline',
          icon: <FileTextOutlined />,
          label: <Link to={`/project/${projectId}/outline`}>大纲管理</Link>,
        },
        {
          key: 'chapters',
          icon: <BookOutlined />,
          label: <Link to={`/project/${projectId}/chapters`}>章节管理</Link>,
        },
        {
          key: 'comic-admin',
          icon: <PictureOutlined />,
          label: <Link to={`/comic-admin/${projectId}`}>漫画管理</Link>,
        },
        {
          key: 'chapter-analysis',
          icon: <FundOutlined />,
          label: <Link to={`/project/${projectId}/chapter-analysis`}>剧情分析</Link>,
        },
        {
          key: 'foreshadows',
          icon: <BulbOutlined />,
          label: <Link to={`/project/${projectId}/foreshadows`}>伏笔管理</Link>,
        },
      ],
    },
    {
      type: 'group' as const,
      label: '创作工具',
      children: [
        {
          key: 'writing-styles',
          icon: <EditOutlined />,
          label: <Link to={`/project/${projectId}/writing-styles`}>写作风格</Link>,
        },
        {
          key: 'comic-style',
          icon: <BgColorsOutlined />,
          label: <Link to={`/project/${projectId}/comic-style`}>漫画风格</Link>,
        },
        {
          key: 'prompt-workshop',
          icon: <CloudOutlined />,
          label: <Link to={`/project/${projectId}/prompt-workshop`}>提示词工坊</Link>,
        },
      ],
    },
  ];

  const menuItemsCollapsed = [
    {
      key: 'world-setting',
      icon: <GlobalOutlined />,
      label: <Link to={`/project/${projectId}/world-setting`}>世界设定</Link>,
    },
    {
      key: 'careers',
      icon: <TrophyOutlined />,
      label: <Link to={`/project/${projectId}/careers`}>职业管理</Link>,
    },
    {
      key: 'characters',
      icon: <TeamOutlined />,
      label: <Link to={`/project/${projectId}/characters`}>角色管理</Link>,
    },
    {
      key: 'relationships',
      icon: <ApartmentOutlined />,
      label: <Link to={`/project/${projectId}/relationships`}>关系管理</Link>,
    },
    {
      key: 'organizations',
      icon: <BankOutlined />,
      label: <Link to={`/project/${projectId}/organizations`}>组织管理</Link>,
    },
    {
      key: 'outline',
      icon: <FileTextOutlined />,
      label: <Link to={`/project/${projectId}/outline`}>大纲管理</Link>,
    },
    {
      key: 'chapters',
      icon: <BookOutlined />,
      label: <Link to={`/project/${projectId}/chapters`}>章节管理</Link>,
    },
    {
      key: 'comic-admin',
      icon: <PictureOutlined />,
      label: <Link to={`/comic-admin/${projectId}`}>漫画管理</Link>,
    },
    {
      key: 'chapter-analysis',
      icon: <FundOutlined />,
      label: <Link to={`/project/${projectId}/chapter-analysis`}>剧情分析</Link>,
    },
    {
      key: 'foreshadows',
      icon: <BulbOutlined />,
      label: <Link to={`/project/${projectId}/foreshadows`}>伏笔管理</Link>,
    },
    {
      key: 'writing-styles',
      icon: <EditOutlined />,
      label: <Link to={`/project/${projectId}/writing-styles`}>写作风格</Link>,
    },
    {
      key: 'comic-style',
      icon: <BgColorsOutlined />,
      label: <Link to={`/project/${projectId}/comic-style`}>漫画风格</Link>,
    },
    {
      key: 'prompt-workshop',
      icon: <CloudOutlined />,
      label: <Link to={`/project/${projectId}/prompt-workshop`}>提示词工坊</Link>,
    },
  ];

  // 根据当前路径动态确定选中的菜单项
  const selectedKey = useMemo(() => {
    const path = location.pathname;
    if (path.includes('/world-setting')) return 'world-setting';
    if (path.includes('/careers')) return 'careers';
    if (path.includes('/relationships')) return 'relationships';
    if (path.includes('/organizations')) return 'organizations';
    if (path.includes('/outline')) return 'outline';
    if (path.includes('/characters')) return 'characters';
    if (path.includes('/chapter-analysis')) return 'chapter-analysis';
    if (path.includes('/foreshadows')) return 'foreshadows';
    if (path.includes('/chapters')) return 'chapters';
    if (path.includes('/comic-admin')) return 'comic-admin';
    if (path.includes('/writing-styles')) return 'writing-styles';
    if (path.includes('/comic-style')) return 'comic-style';
    if (path.includes('/prompt-workshop')) return 'prompt-workshop';
    // if (path.includes('/polish')) return 'polish';
    return 'world-setting';
  }, [location.pathname]);

  const renderFollowupStatus = () => {
    if (!followupStatus || followupStatus.counts.chapters <= 0) {
      return null;
    }

    if (selectedKey !== 'world-setting') {
      return null;
    }

    const counts = followupStatus.counts;
    const analysis = followupStatus.analysis_tasks;
    const analysisPercent = analysis.total > 0 ? Math.round((analysis.completed / analysis.total) * 100) : 0;
    const needsAction = followupStatus.status === 'needs_action' || followupStatus.followup_state?.status === 'failed';
    const analysisActive = analysis.running > 0 || analysis.pending > 0;
    const analysisFailedOnly = analysis.failed > 0 && !analysisActive;
    const canStartFollowup = needsAction || followupStatus.followup_running;

    if (!needsAction && !followupStatus.followup_running && !analysisActive && !analysisFailedOnly) {
      return null;
    }

    const alertType = followupStatus.followup_running || analysisActive
      ? 'info'
      : needsAction || analysisFailedOnly
        ? 'warning'
        : 'success';
    const statusText = followupStatus.followup_running
      ? '补全中'
      : needsAction
        ? '待补全'
        : analysisActive
          ? '分析中'
          : analysisFailedOnly
            ? '分析有失败'
          : '已完成';

    const metricItems = [
      { label: '世界观', value: followupStatus.world_completed ? '已生成' : '未生成', color: followupStatus.world_completed ? 'green' : 'orange' },
      { label: '职业', value: counts.careers },
      { label: '角色', value: counts.characters },
      { label: '组织', value: counts.organizations },
      { label: '关系', value: counts.relationships },
      { label: '组织成员', value: counts.organization_members },
      { label: '记忆', value: counts.memories },
      { label: '伏笔', value: counts.foreshadows },
    ];

    return (
      <Alert
        type={alertType}
        showIcon
        style={{
          marginBottom: 14,
          flexShrink: 0,
          borderRadius: 12,
          border: `1px solid ${alphaColor(cinnabar, 0.1)}`,
          background: alphaColor(token.colorInfoBg, 0.72),
        }}
        message={
          <Space size={8} wrap>
            <span>项目补全</span>
            <Tag color={followupStatus.followup_running ? 'processing' : needsAction ? 'warning' : 'success'}>
              {statusText}
            </Tag>
            {followupLoading && <SyncOutlined spin />}
          </Space>
        }
        description={
          <div>
            <Space size={[8, 8]} wrap style={{ marginBottom: analysis.total > 0 ? 10 : 0 }}>
              {metricItems.map(item => (
                <Tag key={item.label} color={item.color}>
                  {item.label} {item.value}
                </Tag>
              ))}
            </Space>
            {analysis.total > 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <div style={{ minWidth: 220, flex: '1 1 260px' }}>
                  <Progress percent={analysisPercent} size="small" status={analysis.failed > 0 ? 'exception' : 'active'} />
                </div>
                <Space size={6} wrap>
                  <Tag>章节分析 {analysis.completed}/{analysis.total}</Tag>
                  {analysis.running > 0 && <Tag color="processing">运行 {analysis.running}</Tag>}
                  {analysis.pending > 0 && <Tag color="default">等待 {analysis.pending}</Tag>}
                  {analysis.failed > 0 && <Tag color="error">失败 {analysis.failed}</Tag>}
                </Space>
              </div>
            )}
            {followupStatus.followup_state?.error && (
              <div style={{ color: token.colorError, marginTop: 8, fontSize: 12 }}>
                {followupStatus.followup_state.error.length > 180
                  ? `${followupStatus.followup_state.error.slice(0, 180)}...`
                  : followupStatus.followup_state.error}
              </div>
            )}
          </div>
        }
        action={canStartFollowup ? (
          <Button
            size="small"
            type={needsAction ? 'primary' : 'default'}
            loading={followupStarting}
            disabled={followupStatus.followup_running}
            onClick={resumeProjectFollowup}
          >
            {followupStatus.followup_running ? '执行中' : '启动补全'}
          </Button>
        ) : undefined}
      />
    );
  };

  if (loading || !currentProject) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  // 渲染菜单内容
  const renderMenu = () => (
    <div style={{
      flex: 1,
      overflowY: 'auto',
      overflowX: 'hidden'
    }}>
      <Menu
        mode="inline"
        inlineCollapsed={collapsed}
        selectedKeys={[selectedKey]}
        style={{
          borderRight: 0,
          paddingTop: '12px'
        }}
        items={collapsed ? menuItemsCollapsed : menuItems}
        onClick={() => mobile && setDrawerVisible(false)}
      />
    </div>
  );

  return (
    <Layout
      style={{
        minHeight: '100vh',
        height: '100vh',
        overflow: 'hidden',
        background: `linear-gradient(180deg, ${alphaColor(paper, 0.78)} 0%, ${token.colorBgLayout} 46%)`,
      }}
    >
      <Header style={{
        background: `linear-gradient(135deg, ${teal} 0%, #3D716D 68%, ${alphaColor(cinnabar, 0.76)} 100%)`,
        padding: mobile ? '0 12px' : '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        position: 'fixed',
        top: 0,
        left: mobile ? 0 : (collapsed ? 60 : 220),
        right: 0,
        zIndex: 1000,
        boxShadow: `0 8px 26px ${alphaColor(ink, 0.16)}`,
        height: mobile ? 56 : 70,
        transition: 'left 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
        overflow: 'hidden'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', zIndex: 1 }}>
          {mobile && (
            <Button
              type="text"
              icon={<MenuUnfoldOutlined />}
              onClick={() => setDrawerVisible(true)}
              style={{
                fontSize: '18px',
                color: token.colorWhite,
                width: '36px',
                height: '36px'
              }}
            />
          )}
        </div>

        <h2 style={{
          margin: 0,
          color: token.colorWhite,
          fontSize: mobile ? '16px' : '24px',
          fontWeight: 700,
          textShadow: `0 2px 4px ${alphaColor(token.colorText, 0.2)}`,
          position: mobile ? 'static' : 'absolute',
          left: mobile ? 'auto' : '50%',
          transform: mobile ? 'none' : 'translateX(-50%)',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          flex: mobile ? 1 : 'none',
          textAlign: mobile ? 'center' : 'left',
          paddingLeft: mobile ? '8px' : '0',
          paddingRight: mobile ? '8px' : '0'
        }}>
          {currentProject.title}
        </h2>

        {mobile && (
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/')}
            style={{
              fontSize: '14px',
              color: token.colorWhite,
              height: '36px',
              padding: '0 8px',
              zIndex: 1
            }}
          >
            主页
          </Button>
        )}

        {!mobile && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', zIndex: 1 }}>
            <div style={{ display: 'flex', gap: '16px' }}>
              {[
                { label: '大纲', value: outlines.length, unit: '条' },
                { label: '角色', value: characters.length, unit: '个' },
                { label: '章节', value: chapters.length, unit: '章' },
                { label: '已写', value: currentProject.current_words, unit: '字' },
              ].map((item, index) => (
                <div
                  key={index}
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    backdropFilter: 'blur(4px)',
                    borderRadius: '14px',
                    minWidth: '56px',
                    height: '56px',
                    padding: '0 12px',
                    border: `1px solid ${alphaColor(token.colorWhite, 0.14)}`,
                    background: alphaColor(token.colorWhite, 0.1),
                    boxShadow: `inset 0 1px 0 ${alphaColor(token.colorWhite, 0.16)}, 0 4px 10px ${alphaColor(token.colorText, 0.1)}`,
                    cursor: 'default',
                    transition: 'all 0.3s ease',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.transform = 'translateY(-3px) scale(1.02)';
                    e.currentTarget.style.boxShadow = `inset 0 0 20px ${alphaColor(token.colorWhite, 0.25)}, 0 8px 16px ${alphaColor(token.colorText, 0.15)}`;
                    e.currentTarget.style.border = `1px solid ${alphaColor(token.colorWhite, 0.1)}`;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.transform = 'translateY(0) scale(1)';
                    e.currentTarget.style.boxShadow = `inset 0 0 15px ${alphaColor(token.colorWhite, 0.15)}, 0 4px 10px ${alphaColor(token.colorText, 0.1)}`;
                  }}
                >
                  <span style={{
                    fontSize: '11px',
                    color: alphaColor(token.colorWhite, 0.9),
                    marginBottom: '2px',
                    lineHeight: 1
                  }}>
                    {item.label}
                  </span>
                  <span style={{
                    fontSize: '15px',
                    fontWeight: '600',
                    color: token.colorWhite,
                    lineHeight: 1,
                    fontFamily: 'Monaco, monospace'
                  }}>
                    {item.value > 10000 ? (item.value / 10000).toFixed(1) + 'w' : item.value}
                    <span style={{ fontSize: '10px', marginLeft: '2px', opacity: 0.8 }}>{item.unit}</span>
                  </span>
                </div>
              ))}
            </div>
            {currentProject.id && (
              <ProjectTaskDrawer
                projectId={currentProject.id}
                alphaColor={alphaColor}
              />
            )}
          </div>
        )}
      </Header>

      <Layout style={{ marginTop: mobile ? 56 : 70 }}>
        {mobile ? (
          <Drawer
            title={
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{
                  width: 30,
                  height: 30,
                  background: token.colorPrimary,
                  borderRadius: 8,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: token.colorWhite,
                  fontSize: 16,
                }}>
                  <img src={zhuqueAssetUrls.brandMark} alt={APP_NAME} style={{ width: 24, height: 24, objectFit: 'contain' }} />
                </div>
                <span style={{ fontWeight: 600, fontSize: 16 }}>{APP_NAME}</span>
              </div>
            }
            placement="left"
            onClose={() => setDrawerVisible(false)}
            open={drawerVisible}
            width={280}
            styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
          >
            {renderMenu()}
            <div style={{ padding: 16, borderTop: `1px solid ${token.colorBorderSecondary}` }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: token.colorTextTertiary, marginBottom: 8 }}>
                <span>主题模式</span>
                <span>{resolvedMode === 'dark' ? '深色' : '浅色'}</span>
              </div>
              <ThemeSwitch block />
            </div>
          </Drawer>
        ) : (
          <Sider
            collapsible
            collapsed={collapsed}
            onCollapse={setCollapsed}
            trigger={null}
            width={220}
            collapsedWidth={60}
            style={{
              position: 'fixed',
              left: 0,
              top: 0,
              bottom: 0,
              overflow: 'hidden',
              transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
              height: '100vh',
              background: `linear-gradient(180deg, ${alphaColor(zhuqueColors.paperSoft, 0.92)} 0%, ${alphaColor(token.colorBgContainer, 0.96)} 100%)`,
              borderRight: `1px solid ${alphaColor(cinnabar, 0.1)}`,
              boxShadow: `8px 0 28px ${alphaColor(ink, 0.08)}`,
              zIndex: 1000
            }}
          >
            <div style={{
              height: '100%',
              display: 'flex',
              flexDirection: 'column'
            }}>
              <div style={{
                height: 70,
                display: 'flex',
                alignItems: 'center',
                padding: collapsed ? 0 : '0 12px',
                background: `linear-gradient(135deg, ${teal} 0%, #356B67 100%)`,
                flexShrink: 0,
                justifyContent: collapsed ? 'center' : 'space-between',
                gap: 8
              }}>
                {collapsed ? (
                  <Button
                    type="text"
                    icon={<MenuUnfoldOutlined />}
                    onClick={() => setCollapsed(false)}
                    style={{
                      color: token.colorWhite,
                      width: '100%',
                      height: '100%',
                      padding: 0,
                      borderRadius: 0,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}
                  />
                ) : (
                  <>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0, overflow: 'hidden' }}>
                      <div style={{
                        width: 30,
                        height: 30,
                        background: `linear-gradient(135deg, ${alphaColor(token.colorWhite, 0.24)} 0%, ${alphaColor(gold, 0.34)} 100%)`,
                        borderRadius: 8,
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        color: token.colorWhite,
                        fontSize: 16,
                        backdropFilter: 'blur(4px)'
                      }}>
                        <img src={zhuqueAssetUrls.brandMark} alt={APP_NAME} style={{ width: 24, height: 24, objectFit: 'contain' }} />
                      </div>
                      <span style={{
                        color: token.colorWhite,
                        fontWeight: 600,
                        fontSize: 15,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis'
                      }}>
                        {APP_NAME}
                      </span>
                    </div>
                    <Button
                      type="text"
                      icon={<MenuFoldOutlined />}
                      onClick={() => setCollapsed(true)}
                      style={{
                        color: token.colorWhite,
                        width: 32,
                        height: 32,
                        padding: 0,
                        flexShrink: 0
                      }}
                    />
                  </>
                )}
              </div>
              {renderMenu()}
              <div style={{
                padding: collapsed ? '12px 8px' : '12px',
                borderTop: `1px solid ${token.colorBorderSecondary}`,
                flexShrink: 0
              }}>
                {collapsed ? (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
                    <Button
                      type="text"
                      icon={collapsedThemeIcon}
                      onClick={cycleThemeMode}
                      title={`主题模式：${mode === 'light' ? '浅色' : mode === 'dark' ? '深色' : '跟随系统'}（点击切换）`}
                      style={{
                        width: 40,
                        height: 40,
                        borderRadius: 20,
                        background: alphaColor(token.colorBgContainer, 0.65),
                        border: `1px solid ${token.colorBorder}`,
                        color: token.colorText,
                        padding: 0,
                      }}
                    />
                    <Button
                      type="text"
                      icon={<ArrowLeftOutlined />}
                      onClick={() => navigate('/')}
                      style={{
                        width: 40,
                        height: 40,
                        borderRadius: 20,
                        background: alphaColor(token.colorBgContainer, 0.65),
                        border: `1px solid ${token.colorBorder}`,
                        color: token.colorText,
                        padding: 0,
                      }}
                    />
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: token.colorTextTertiary }}>
                      <span>主题模式</span>
                      <span>{resolvedMode === 'dark' ? '深色' : '浅色'}</span>
                    </div>
                    <ThemeSwitch block />
                    <Button
                      type="text"
                      icon={<ArrowLeftOutlined />}
                      onClick={() => navigate('/')}
                      block
                      style={{
                        color: token.colorText,
                        height: 40,
                        justifyContent: 'flex-start',
                        padding: '0 12px'
                      }}
                    >
                      返回主页
                    </Button>
                  </div>
                )}
              </div>
            </div>
          </Sider>
        )}

        <Layout style={{
          marginLeft: mobile ? 0 : (collapsed ? 60 : 220),
          transition: 'margin-left 0.3s cubic-bezier(0.4, 0, 0.2, 1)'
        }}>
          <Content
            style={{
              background: `
                linear-gradient(${alphaColor(paper, 0.68)}, ${alphaColor(paper, 0.68)}),
                url(${zhuqueAssetUrls.paperTexture}),
                linear-gradient(${alphaColor(ink, 0.032)} 1px, transparent 1px),
                linear-gradient(90deg, ${alphaColor(ink, 0.028)} 1px, transparent 1px),
                linear-gradient(180deg, ${alphaColor(paper, 0.68)} 0%, ${token.colorBgLayout} 42%)
              `,
              backgroundSize: 'auto, cover, 52px 52px, 52px 52px, auto',
              padding: mobile ? 12 : 24,
              height: mobile ? 'calc(100vh - 56px)' : 'calc(100vh - 70px)',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column'
            }}
          >
            <div style={{
              background: alphaColor(token.colorBgContainer, 0.86),
              padding: mobile ? 12 : 24,
              borderRadius: mobile ? '8px' : '16px',
              border: `1px solid ${alphaColor(cinnabar, 0.08)}`,
              boxShadow: `0 18px 42px ${alphaColor(ink, 0.1)}`,
              height: '100%',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column'
            }}>
              {renderFollowupStatus()}
              <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
                <Outlet />
              </div>
            </div>
          </Content>
        </Layout>
      </Layout>
    </Layout>
  );
}
