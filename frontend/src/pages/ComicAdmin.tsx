import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  Alert,
  Button,
  Card,
  Empty,
  InputNumber,
  Layout,
  Pagination,
  Segmented,
  Progress,
  Spin,
  Space,
  Tag,
  Typography,
  theme,
} from 'antd';
import { ArrowLeftOutlined, ReloadOutlined, ReadOutlined, PictureOutlined } from '@ant-design/icons';
import { comicApi, projectApi } from '../services/api';
import type {
  ComicBatchGenerateStatusResponse,
  ComicProjectChapterStatus,
  ComicContinuousReadResponse,
  Project,
} from '../types';
import ComicChapterDrawer, { type ComicChapterDrawerTarget } from '../components/ComicChapterDrawer';
import { buildApiPath } from '../utils/basePath';

const isMobile = () => window.innerWidth <= 768;

const { Header, Content } = Layout;
const { Title, Text } = Typography;

type AdminView = 'manage' | 'read';
const COMIC_ADMIN_BATCH_POLL_INTERVAL_MS = 5000;

const resolveComicImageUrl = (url?: string | null) => {
  if (!url) return '';
  if (/^https?:\/\//i.test(url)) return url;
  return buildApiPath(url);
};

const appendCacheParam = (url: string, updatedAt?: string | null) => {
  const separator = url.includes('?') ? '&' : '?';
  return `${url}${separator}t=${encodeURIComponent(updatedAt || '')}`;
};

const getComicStatusColor = (status?: string | null) => {
  const colors: Record<string, string> = {
    missing: 'default',
    available: 'processing',
    edited: 'gold',
    queued: 'gold',
    running: 'processing',
    ready: 'success',
    completed: 'success',
    partial: 'warning',
    failed: 'error',
  };
  return colors[status || ''] || 'default';
};

const getComicStatusLabel = (status?: string | null) => {
  const labels: Record<string, string> = {
    missing: '缺失',
    available: '可用',
    edited: '已编辑',
    queued: '排队中',
    running: '生成中',
    ready: '已生成',
    completed: '已完成',
    partial: '部分完成',
    failed: '失败',
  };
  return labels[status || ''] || status || '未知';
};

export default function ComicAdmin() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [comicProject, setComicProject] = useState<{ chapters: ComicProjectChapterStatus[]; summary: { chapter_count: number; storyboard_count: number; image_page_count: number; failed_page_count: number } } | null>(null);
  const [readingData, setReadingData] = useState<ComicContinuousReadResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [readingLoading, setReadingLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [projectListLoading, setProjectListLoading] = useState(false);
  const [mobile, setMobile] = useState(isMobile());
  const [drawerChapter, setDrawerChapter] = useState<ComicChapterDrawerTarget | null>(null);
  const [managePage, setManagePage] = useState(1);
  const [managePageSize, setManagePageSize] = useState(20);
  const [readPage, setReadPage] = useState(1);
  const [readPageSize, setReadPageSize] = useState(3);
  const [comicBatchStartChapter, setComicBatchStartChapter] = useState(1);
  const [comicBatchCount, setComicBatchCount] = useState(1);
  const [comicBatchConcurrency, setComicBatchConcurrency] = useState(2);
  const [comicBatchTaskId, setComicBatchTaskId] = useState<string | null>(null);
  const [comicBatchGenerating, setComicBatchGenerating] = useState(false);
  const [comicBatchProgress, setComicBatchProgress] = useState<ComicBatchGenerateStatusResponse | null>(null);
  const comicBatchPollRef = useRef<number | null>(null);
  const comicBatchLastRefreshCompletedRef = useRef<number | null>(null);
  const { token } = theme.useToken();
  const view = (searchParams.get('mode') === 'read' ? 'read' : 'manage') as AdminView;
  const anchorChapter = searchParams.get('chapter');
  const anchorRef = useRef<string | null>(anchorChapter);

  const refreshData = useCallback(async (quiet = false) => {
    if (!projectId) {
      try {
        setProjectListLoading(true);
        const response = await projectApi.getProjects();
        const data = response as Project[] | { items?: Project[] };
        setProjects(Array.isArray(data) ? data : data.items || []);
      } finally {
        setProjectListLoading(false);
        setLoading(false);
      }
      return;
    }
    try {
      if (!quiet) {
        setRefreshing(true);
      }
      if (view === 'read') {
        setReadingLoading(true);
      }
      const [projectResponse, comicResponse, readingResponse] = await Promise.all([
        projectApi.getProject(projectId, { silent: quiet }),
        comicApi.getProjectChapters(projectId, { silent: quiet }),
        view === 'read' ? comicApi.getContinuousRead(projectId, { silent: quiet }) : Promise.resolve(null),
      ]);
      setProject(projectResponse);
      setComicProject(comicResponse);
      if (readingResponse) {
        setReadingData(readingResponse);
      } else if (view !== 'read') {
        setReadingData(null);
      }
    } finally {
      setLoading(false);
      setReadingLoading(false);
      setRefreshing(false);
    }
  }, [projectId, view]);

  useEffect(() => {
    const handleResize = () => setMobile(isMobile());
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  useEffect(() => {
    void refreshData();
  }, [projectId, refreshData]);

  useEffect(() => {
    anchorRef.current = view === 'read' ? anchorChapter : null;
  }, [anchorChapter, view]);

  useEffect(() => {
    if (view !== 'read' || !readingData || !anchorRef.current) {
      return;
    }

    const timer = window.setTimeout(() => {
      const element = document.getElementById(`comic-chapter-${anchorRef.current}`);
      element?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      anchorRef.current = null;
    }, 50);

    return () => window.clearTimeout(timer);
  }, [anchorChapter, readingData, readPage, view]);

  const sortedChapters = useMemo(() => {
    return [...(comicProject?.chapters || [])].sort((a, b) => a.chapter_number - b.chapter_number);
  }, [comicProject?.chapters]);

  const pagedManageChapters = useMemo(() => {
    const start = (managePage - 1) * managePageSize;
    return sortedChapters.slice(start, start + managePageSize);
  }, [managePage, managePageSize, sortedChapters]);

  const readChapters = useMemo(() => readingData?.chapters || [], [readingData?.chapters]);
  const pagedReadChapters = useMemo(() => {
    const start = (readPage - 1) * readPageSize;
    return readChapters.slice(start, start + readPageSize);
  }, [readChapters, readPage, readPageSize]);

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(sortedChapters.length / managePageSize));
    if (managePage > maxPage) {
      setManagePage(maxPage);
    }
  }, [managePage, managePageSize, sortedChapters.length]);

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(readChapters.length / readPageSize));
    if (readPage > maxPage) {
      setReadPage(maxPage);
    }
  }, [readChapters.length, readPage, readPageSize]);

  useEffect(() => {
    if (view !== 'read' || !anchorChapter || readChapters.length === 0) {
      return;
    }
    const index = readChapters.findIndex((chapter) => String(chapter.chapter_number) === anchorChapter);
    if (index >= 0) {
      setReadPage(Math.floor(index / readPageSize) + 1);
    }
  }, [anchorChapter, readChapters, readPageSize, view]);

  const stopComicBatchPolling = useCallback(() => {
    if (comicBatchPollRef.current) {
      window.clearInterval(comicBatchPollRef.current);
      comicBatchPollRef.current = null;
    }
  }, []);

  const startComicBatchPolling = useCallback((taskId: string) => {
    stopComicBatchPolling();
    comicBatchLastRefreshCompletedRef.current = null;

    const poll = async () => {
      try {
        if (!projectId) {
          return;
        }
        const status = await comicApi.getComicBatchGenerateStatus(projectId, taskId, { silent: true });
        setComicBatchProgress(status);
        const isTerminal = status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled';
        if (status.completed !== comicBatchLastRefreshCompletedRef.current || isTerminal) {
          comicBatchLastRefreshCompletedRef.current = status.completed;
          await refreshData(true);
        }

        if (isTerminal) {
          stopComicBatchPolling();
          setComicBatchGenerating(false);
          const errorCount = status.errors?.length || 0;
          const skippedCount = status.skipped_chapters?.length || 0;
          if (status.status === 'completed' && errorCount === 0) {
            void import('antd').then(({ message }) => {
              message.success(`批量漫画生成完成${skippedCount > 0 ? `，跳过 ${skippedCount} 章` : ''}`);
            });
          } else if (status.status === 'completed') {
            void import('antd').then(({ message }) => {
              message.warning(`批量漫画生成完成，但有 ${errorCount} 章处理失败`);
            });
          } else {
            void import('antd').then(({ message }) => {
              message.error(status.error || '批量漫画生成失败');
            });
          }
        }
      } catch (error) {
        console.error('轮询批量漫画生成状态失败:', error);
      }
    };

    void poll();
    comicBatchPollRef.current = window.setInterval(poll, COMIC_ADMIN_BATCH_POLL_INTERVAL_MS);
  }, [projectId, refreshData, stopComicBatchPolling]);

  const handleBatchGenerateComics = useCallback(async () => {
    const resolvedProjectId = projectId;
    if (!resolvedProjectId || !comicProject) {
      return;
    }

    const totalChapters = comicProject.summary.chapter_count || 0;
    if (totalChapters === 0) {
      void import('antd').then(({ message }) => message.warning('当前项目没有可批量生成的章节'));
      return;
    }

    const startChapterNumber = Math.max(1, Math.min(comicBatchStartChapter || 1, totalChapters));
    const maxCount = Math.max(1, totalChapters - startChapterNumber + 1);
    const count = Math.max(1, Math.min(comicBatchCount || 1, maxCount));

    try {
      setComicBatchGenerating(true);
      const result = await comicApi.batchGenerateComics(resolvedProjectId, startChapterNumber, count, comicBatchConcurrency);
      setComicBatchTaskId(result.task_id);
      setComicBatchProgress({
        task_id: result.task_id,
        project_id: resolvedProjectId,
        type: 'batch',
        status: result.status,
        total: result.total,
        completed: 0,
        current_chapter_number: result.chapter_numbers[0] ?? null,
        chapter_numbers: result.chapter_numbers,
        errors: [],
        skipped_chapters: [],
        chapter_results: [],
      });
      void import('antd').then(({ message }) => message.success(result.message));
      startComicBatchPolling(result.task_id);
    } catch (error) {
      console.error('批量漫画生成失败:', error);
      setComicBatchGenerating(false);
      void import('antd').then(({ message }) => message.error('批量漫画生成失败'));
    }
  }, [comicBatchConcurrency, comicBatchCount, comicBatchStartChapter, comicProject, projectId, startComicBatchPolling]);

  useEffect(() => {
    return () => {
      stopComicBatchPolling();
    };
  }, [stopComicBatchPolling]);

  const renderManageView = () => {
    if (!comicProject) {
      return <Empty description="暂无漫画数据" />;
    }

    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card size="small" title="批量生成漫画">
          <Space wrap align="end">
            <Space direction="vertical" size={4}>
              <Text type="secondary">起始章节</Text>
              <InputNumber
                min={1}
                max={comicProject.summary.chapter_count || 1}
                value={comicBatchStartChapter}
                onChange={(value) => setComicBatchStartChapter(value || 1)}
                style={{ width: 120 }}
              />
            </Space>
            <Space direction="vertical" size={4}>
              <Text type="secondary">章节数量</Text>
              <InputNumber
                min={1}
                max={comicProject.summary.chapter_count || 1}
                value={comicBatchCount}
                onChange={(value) => setComicBatchCount(value || 1)}
                style={{ width: 120 }}
              />
            </Space>
            <Space direction="vertical" size={4}>
              <Text type="secondary">漫画并发</Text>
              <InputNumber
                min={1}
                max={6}
                value={comicBatchConcurrency}
                onChange={(value) => setComicBatchConcurrency(value || 2)}
                style={{ width: 120 }}
              />
            </Space>
            <Button
              type="primary"
              icon={<PictureOutlined />}
              onClick={() => void handleBatchGenerateComics()}
              loading={comicBatchGenerating}
            >
              批量生成
            </Button>
          </Space>
          {comicBatchProgress && (
            <div style={{ marginTop: 16 }}>
              <Progress
                percent={comicBatchProgress.total > 0 ? Math.round((comicBatchProgress.completed / comicBatchProgress.total) * 100) : 0}
                status={comicBatchProgress.status === 'failed' ? 'exception' : 'active'}
              />
              <Alert
                type={comicBatchProgress.status === 'failed' ? 'error' : 'info'}
                showIcon
                style={{ marginTop: 12 }}
                message={
                  comicBatchProgress.status === 'completed'
                    ? '批量漫画生成完成'
                    : `批量漫画生成中：第 ${comicBatchProgress.current_chapter_number || '-'} 章`
                }
                description={(
                  <Space direction="vertical" size={4}>
                    {comicBatchTaskId && (
                      <Text type="secondary">
                        任务 {comicBatchTaskId.slice(0, 8)}
                      </Text>
                    )}
                    <Text type="secondary">
                      进度 {comicBatchProgress.completed}/{comicBatchProgress.total}
                    </Text>
                    {comicBatchProgress.skipped_chapters && comicBatchProgress.skipped_chapters.length > 0 && (
                      <Text type="secondary">
                        跳过 {comicBatchProgress.skipped_chapters.length} 章
                      </Text>
                    )}
                    {comicBatchProgress.errors && comicBatchProgress.errors.length > 0 && (
                      <Text type="danger">
                        失败 {comicBatchProgress.errors.length} 章
                      </Text>
                    )}
                  </Space>
                )}
              />
            </div>
          )}
        </Card>
        <Alert
          type="info"
          showIcon
          message="这里展示所有章节的漫画与分镜状态，重生成会先返回 queued/running，再通过状态轮询更新。"
        />
        <Card size="small">
          <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space wrap>
              <Tag color="blue">章节 {comicProject.summary.chapter_count}</Tag>
              <Tag color="green">分镜 {comicProject.summary.storyboard_count}</Tag>
              <Tag color="gold">图片 {comicProject.summary.image_page_count}</Tag>
              <Tag color="red">失败 {comicProject.summary.failed_page_count}</Tag>
            </Space>
            <Text type="secondary">
              当前 {pagedManageChapters.length} / {sortedChapters.length} 章
            </Text>
          </Space>
        </Card>
        <div style={{
          display: 'grid',
          gap: 16,
          gridTemplateColumns: mobile ? '1fr' : 'repeat(2, minmax(0, 1fr))',
        }}>
          {pagedManageChapters.map((chapter) => (
            <Card
              key={chapter.chapter_number}
              title={`第 ${chapter.chapter_number} 章${chapter.chapter_title ? ` · ${chapter.chapter_title}` : ''}`}
              extra={(
                <Space>
                  <Button size="small" icon={<ReadOutlined />} onClick={() => setSearchParams({ mode: 'read', chapter: String(chapter.chapter_number) })}>
                    连续阅读
                  </Button>
                  <Button size="small" icon={<PictureOutlined />} type="primary" onClick={() => setDrawerChapter({ chapter_number: chapter.chapter_number, title: chapter.chapter_title })}>
                    管理
                  </Button>
                </Space>
              )}
            >
              <Space wrap>
                <Tag color={getComicStatusColor(chapter.chapter_status)}>
                  漫画：{getComicStatusLabel(chapter.chapter_status)}
                </Tag>
                <Tag color={getComicStatusColor(chapter.storyboard.status)}>
                  分镜：{getComicStatusLabel(chapter.storyboard.status)}
                </Tag>
                <Tag color="blue">{chapter.available_page_count}/{chapter.page_count} 页</Tag>
              </Space>
              <div style={{ marginTop: 12, color: token.colorTextSecondary }}>
                {chapter.chapter_title || '未命名章节'}
              </div>
            </Card>
          ))}
        </div>
        {sortedChapters.length > managePageSize && (
          <Pagination
            current={managePage}
            pageSize={managePageSize}
            total={sortedChapters.length}
            showSizeChanger
            pageSizeOptions={[10, 20, 50, 100]}
            showTotal={(total, range) => `第 ${range[0]}-${range[1]} 章 / 共 ${total} 章`}
            onChange={(page, pageSize) => {
              setManagePage(page);
              setManagePageSize(pageSize);
            }}
            style={{ alignSelf: 'flex-end' }}
          />
        )}
      </Space>
    );
  };

  const renderReadView = () => {
    if (readingLoading) {
      return (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '50vh' }}>
          <Spin size="large" />
        </div>
      );
    }

    if (!readingData) {
      return <Empty description="暂无可连续阅读的漫画页" />;
    }

    return (
      <Space direction="vertical" size={20} style={{ width: '100%' }}>
        <Alert
          type="success"
          showIcon
          message={`连续阅读模式：共 ${readingData.summary.chapter_count} 章，${readingData.summary.page_count} 张图`}
        />
        {readingData.chapters.length > readPageSize && (
          <Pagination
            current={readPage}
            pageSize={readPageSize}
            total={readingData.chapters.length}
            showSizeChanger
            pageSizeOptions={[1, 3, 5, 10]}
            showTotal={(total, range) => `第 ${range[0]}-${range[1]} 章 / 共 ${total} 章`}
            onChange={(page, pageSize) => {
              setReadPage(page);
              setReadPageSize(pageSize);
            }}
          />
        )}
        {pagedReadChapters.map((chapter) => (
          <Card
            key={chapter.chapter_number}
            id={`comic-chapter-${chapter.chapter_number}`}
            title={`第 ${chapter.chapter_number} 章${chapter.chapter_title ? ` · ${chapter.chapter_title}` : ''}`}
            extra={<Tag color={getComicStatusColor(chapter.chapter_status)}>{getComicStatusLabel(chapter.chapter_status)}</Tag>}
          >
            <div style={{
              display: 'grid',
              gap: 16,
              gridTemplateColumns: mobile ? '1fr' : 'repeat(2, minmax(0, 1fr))',
            }}>
              {chapter.pages.map((page) => (
                <div key={`${chapter.chapter_number}-${page.page_number}`} style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 12, overflow: 'hidden' }}>
                  <div style={{ padding: '10px 12px', borderBottom: `1px solid ${token.colorBorderSecondary}`, display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                    <Text strong>第 {page.page_number} 页</Text>
                    <Tag color={getComicStatusColor(page.status)}>{getComicStatusLabel(page.status)}</Tag>
                  </div>
                    <img
                      src={appendCacheParam(resolveComicImageUrl(page.image_url), page.updated_at)}
                      alt={`第 ${chapter.chapter_number} 章第 ${page.page_number} 页`}
                      style={{ width: '100%', display: 'block', background: token.colorBgContainer }}
                    />
                </div>
              ))}
            </div>
          </Card>
        ))}
        {readingData.chapters.length > readPageSize && (
          <Pagination
            current={readPage}
            pageSize={readPageSize}
            total={readingData.chapters.length}
            showSizeChanger
            pageSizeOptions={[1, 3, 5, 10]}
            showTotal={(total, range) => `第 ${range[0]}-${range[1]} 章 / 共 ${total} 章`}
            onChange={(page, pageSize) => {
              setReadPage(page);
              setReadPageSize(pageSize);
            }}
            style={{ alignSelf: 'flex-end' }}
          />
        )}
      </Space>
    );
  };

  const renderProjectPicker = () => {
    if (projectListLoading) {
      return (
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
          <Spin size="large" />
        </div>
      );
    }

    if (projects.length === 0) {
      return <Empty description="暂无可管理项目" />;
    }

    return (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert
          type="info"
          showIcon
          message="请选择一个项目进入漫画管理"
        />
        <div style={{
          display: 'grid',
          gap: 16,
          gridTemplateColumns: mobile ? '1fr' : 'repeat(2, minmax(0, 1fr))',
        }}>
          {projects.map((item) => (
            <Card
              key={item.id}
              title={item.title}
              extra={<Button type="primary" onClick={() => navigate(`/comic-admin/${item.id}`)}>进入</Button>}
            >
              <Space direction="vertical" size={8}>
                <Text type="secondary">{item.description || '未填写简介'}</Text>
                <Space wrap>
                  <Tag color="blue">{item.status || 'unknown'}</Tag>
                  <Tag color="green">{item.current_words || 0} 字</Tag>
                </Space>
              </Space>
            </Card>
          ))}
        </div>
      </Space>
    );
  };

  if (!projectId) {
    return (
      <Layout style={{ minHeight: '100vh', background: token.colorBgLayout }}>
        <Header style={{
          background: token.colorPrimary,
          color: token.colorWhite,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          padding: mobile ? '0 12px' : '0 24px',
          position: 'sticky',
          top: 0,
          zIndex: 10,
        }}>
          <Space>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/projects')}
            >
              返回书架
            </Button>
            <Title level={4} style={{ color: token.colorWhite, margin: 0 }}>
              漫画管理
            </Title>
          </Space>
          <Button icon={<ReloadOutlined />} onClick={() => void refreshData()} loading={refreshing}>
            刷新
          </Button>
        </Header>

        <Content style={{ padding: mobile ? 12 : 24 }}>
          <div style={{ maxWidth: 1400, margin: '0 auto' }}>
            {renderProjectPicker()}
          </div>
        </Content>
      </Layout>
    );
  }

  if (loading || !project) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <Layout style={{ minHeight: '100vh', background: token.colorBgLayout }}>
      <Header style={{
        background: token.colorPrimary,
        color: token.colorWhite,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        padding: mobile ? '0 12px' : '0 24px',
        position: 'sticky',
        top: 0,
        zIndex: 10,
      }}>
        <Space>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate(`/project/${projectId}/chapters`)}
          >
            返回章节
          </Button>
          <Title level={4} style={{ color: token.colorWhite, margin: 0 }}>
            {project.title} · 漫画管理
          </Title>
        </Space>
        <Space wrap>
          <Segmented
            value={view}
            onChange={(value) => setSearchParams(value === 'read' ? { mode: 'read' } : {})}
            options={[
              { label: '管理', value: 'manage' },
              { label: '连续阅读', value: 'read' },
            ]}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void refreshData()} loading={refreshing}>
            刷新
          </Button>
        </Space>
      </Header>

      <Content style={{ padding: mobile ? 12 : 24 }}>
        <div style={{ maxWidth: 1400, margin: '0 auto' }}>
          {view === 'read' ? renderReadView() : renderManageView()}
        </div>
      </Content>

      <ComicChapterDrawer
        projectId={projectId}
        chapter={drawerChapter}
        open={drawerChapter !== null}
        onClose={() => setDrawerChapter(null)}
        mobile={mobile}
        onChapterStatusChange={() => {
          void refreshData(true);
        }}
      />
    </Layout>
  );
}
