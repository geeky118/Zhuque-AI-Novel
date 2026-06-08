import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Badge, Button, Card, Drawer, Empty, Modal, Radio, Space, Spin, Tag, Typography, theme } from 'antd';
import { CloseOutlined, ExclamationCircleOutlined, LoadingOutlined, PictureOutlined, ReadOutlined, RobotOutlined, SaveOutlined, SyncOutlined } from '@ant-design/icons';
import TextArea from 'antd/es/input/TextArea';
import { useNavigate } from 'react-router-dom';
import { comicApi } from '../services/api';
import { buildAppPath } from '../utils/basePath';
import type {
  ComicChapterCombinedResponse,
  ComicChapterRegenerationStatusResponse,
  ComicPage,
} from '../types';

const { Text } = Typography;

export interface ComicChapterDrawerTarget {
  chapter_number: number;
  title?: string | null;
}

interface ComicChapterDrawerProps {
  projectId: string;
  chapter: ComicChapterDrawerTarget | null;
  open: boolean;
  onClose: () => void;
  mobile?: boolean;
  onChapterStatusChange?: () => void;
}

const getChapterStatusColor = (status: string) => {
  const colors: Record<string, string> = {
    draft: 'default',
    writing: 'processing',
    completed: 'success',
  };
  return colors[status] || 'default';
};

const getChapterStatusText = (status: string) => {
  const texts: Record<string, string> = {
    draft: '草稿',
    writing: '创作中',
    completed: '已完成',
  };
  return texts[status] || status;
};

const getComicStatusColor = (status?: string) => {
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

const getComicStatusLabel = (status?: string) => {
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

const getComicPageErrorText = (page: ComicPage) => {
  if (page.error_message) {
    return page.error_message;
  }
  const workerError = page.regeneration?.worker_error;
  if (typeof workerError === 'string' && workerError.trim()) {
    return workerError.trim();
  }
  const errorMessage = page.regeneration?.error_message;
  if (typeof errorMessage === 'string' && errorMessage.trim()) {
    return errorMessage.trim();
  }
  const responseExcerpt = page.failed_metadata?.response_excerpt;
  if (typeof responseExcerpt === 'string' && responseExcerpt.trim()) {
    return responseExcerpt.trim();
  }
  const category = page.failed_metadata?.category;
  if (typeof category === 'string' && category.trim()) {
    return category.trim();
  }
  return page.status === 'failed' ? '生成失败，但后端没有返回具体错误。' : null;
};

const compactComicErrorText = (text: string) => (text.length > 180 ? `${text.slice(0, 180)}...` : text);

export default function ComicChapterDrawer({
  projectId,
  chapter,
  open,
  onClose,
  mobile = false,
  onChapterStatusChange,
}: ComicChapterDrawerProps) {
  const navigate = useNavigate();
  const { token } = theme.useToken();
  const [comicData, setComicData] = useState<ComicChapterCombinedResponse | null>(null);
  const comicDataRef = useRef(comicData);
  comicDataRef.current = comicData;
  const [comicLoading, setComicLoading] = useState(false);
  const [comicSaving, setComicSaving] = useState(false);
  const [comicChapterRegenerating, setComicChapterRegenerating] = useState(false);
  const [comicPageRegenerating, setComicPageRegenerating] = useState<Record<number, boolean>>({});
  const [storyboardEditorMode, setStoryboardEditorMode] = useState<'markdown' | 'json'>('markdown');
  const [storyboardMarkdown, setStoryboardMarkdown] = useState('');
  const [storyboardJsonText, setStoryboardJsonText] = useState('');
  const [storyboardGenerating, setStoryboardGenerating] = useState(false);
  const storyboardGenPollRef = useRef<number | null>(null);
  const [previewPage, setPreviewPage] = useState<{ url: string; title: string } | null>(null);
  const [editPageModal, setEditPageModal] = useState<{ pageNumber: number } | null>(null);
  const [editPagePrompt, setEditPagePrompt] = useState('');
  const [comicPageEditing, setComicPageEditing] = useState<Record<number, boolean>>({});
  const pendingPageOperationsRef = useRef<Record<number, string>>({});
  const notifiedPageFailuresRef = useRef<Record<number, string>>({});

  const hydrateComicEditor = useCallback((payload: ComicChapterCombinedResponse) => {
    const markdown = payload.storyboard.markdown_content || '';
    const jsonText = payload.storyboard.json_text
      || (payload.storyboard.json_content ? JSON.stringify(payload.storyboard.json_content, null, 2) : '');

    setStoryboardMarkdown(markdown);
    setStoryboardJsonText(jsonText);
    setStoryboardEditorMode(markdown.trim() ? 'markdown' : 'json');
  }, []);

  const loadComicChapter = useCallback(async (options?: { syncEditor?: boolean; silent?: boolean; background?: boolean }) => {
    if (!chapter) return;

    const { syncEditor = false, silent = false, background = false } = options || {};
    try {
      if (!background) {
        setComicLoading(true);
      }
      const response = await comicApi.getChapterCombined(projectId, chapter.chapter_number, {
        silent,
      });
      setComicData(response);
      if (syncEditor || !comicDataRef.current) {
        hydrateComicEditor(response);
      }
    } catch (error) {
      console.error('加载漫画章节失败:', error);
      if (!silent) {
        void import('antd').then(({ message }) => {
          message.error('加载漫画/分镜失败');
        });
      }
    } finally {
      if (!background) {
        setComicLoading(false);
      }
    }
  }, [chapter, hydrateComicEditor, projectId]);

  const updateComicStatusOnly = useCallback((statusPayload: ComicChapterRegenerationStatusResponse) => {
    setComicData((prev) => {
      if (!prev) {
        return prev;
      }
      return {
        ...prev,
        comic: {
          pages: statusPayload.pages,
          page_count: statusPayload.page_count,
          available_page_count: statusPayload.available_page_count,
          chapter_status: statusPayload.chapter_status,
        },
      };
    });
  }, []);

  useEffect(() => {
    if (!open || !chapter) {
      setComicData(null);
      setComicPageRegenerating({});
      pendingPageOperationsRef.current = {};
      notifiedPageFailuresRef.current = {};
      setPreviewPage(null);
      return;
    }
    void loadComicChapter({ syncEditor: true });
  }, [chapter, loadComicChapter, open]);

  useEffect(() => {
    if (!comicData) {
      return;
    }

    for (const page of comicData.comic.pages) {
      const operationLabel = pendingPageOperationsRef.current[page.page_number];
      if (!operationLabel) {
        continue;
      }
      if (page.status === 'queued' || page.status === 'running') {
        continue;
      }

      const errorText = getComicPageErrorText(page);
      if (page.failed && errorText) {
        const notificationKey = `${operationLabel}:${errorText}`;
        if (notifiedPageFailuresRef.current[page.page_number] !== notificationKey) {
          notifiedPageFailuresRef.current[page.page_number] = notificationKey;
          void import('antd').then(({ message }) => {
            message.error(`第 ${page.page_number} 页${operationLabel}失败：${compactComicErrorText(errorText)}`, 8);
          });
        }
        delete pendingPageOperationsRef.current[page.page_number];
        continue;
      }

      if (page.image_available && (page.status === 'ready' || page.status === 'completed')) {
        delete pendingPageOperationsRef.current[page.page_number];
      }
    }
  }, [comicData]);

  const activeStatusSummary = useMemo(() => {
    if (!comicData) {
      return null;
    }
    const queuedPages = comicData.comic.pages.filter((page) => page.status === 'queued').length;
    const runningPages = comicData.comic.pages.filter((page) => page.status === 'running').length;
    if (queuedPages === 0 && runningPages === 0) {
      return null;
    }
    return { queuedPages, runningPages };
  }, [comicData]);

  useEffect(() => {
    if (!open || !chapter || !activeStatusSummary) {
      return undefined;
    }

    const timer = window.setInterval(async () => {
      try {
        await loadComicChapter({ silent: true, background: true });
      } catch (error) {
        console.error('轮询漫画/分镜状态失败:', error);
      }
    }, 5000);

    return () => window.clearInterval(timer);
  }, [activeStatusSummary, chapter, loadComicChapter, open]);

  const handleSaveStoryboard = async () => {
    if (!chapter) return;
    const { message } = await import('antd');

    try {
      setComicSaving(true);
      let normalizedJsonText: string | null = null;
      if (storyboardJsonText.trim()) {
        normalizedJsonText = JSON.stringify(JSON.parse(storyboardJsonText), null, 2);
      }

      await comicApi.updateChapterStoryboard(projectId, chapter.chapter_number, {
        markdown_content: storyboardMarkdown,
        json_text: normalizedJsonText,
      });

      message.success('分镜保存成功');
      await loadComicChapter({ syncEditor: true, silent: true });
      onChapterStatusChange?.();
    } catch (error) {
      console.error('保存分镜失败:', error);
      message.error('保存分镜失败，请检查 JSON 格式');
    } finally {
      setComicSaving(false);
    }
  };

  const handleRegenerateComicChapter = async () => {
    if (!chapter) return;
    const { message } = await import('antd');

    try {
      setComicChapterRegenerating(true);
      const result = await comicApi.regenerateChapter(projectId, chapter.chapter_number);
      const actionText = result.status === 'running'
        ? '本章已有页面正在生成，已返回当前运行状态'
        : result.status === 'queued'
          ? `已提交本章漫画重生成任务${result.queued_count ? `（${result.queued_count} 页）` : ''}`
          : '已更新本章漫画任务状态';
      message.success(actionText);

      const statusPayload = await comicApi.getChapterRegenerationStatus(projectId, chapter.chapter_number);
      updateComicStatusOnly(statusPayload);
      onChapterStatusChange?.();
    } catch (error) {
      console.error('整章重生成失败:', error);
      message.error('整章重生成失败');
    } finally {
      setComicChapterRegenerating(false);
    }
  };

  const handleRegenerateComicPage = async (pageNumber: number) => {
    if (!chapter) return;
    const { message } = await import('antd');

    try {
      setComicPageRegenerating((prev) => ({ ...prev, [pageNumber]: true }));
      pendingPageOperationsRef.current[pageNumber] = '重生图';
      const result = await comicApi.regeneratePage(projectId, chapter.chapter_number, pageNumber);
      message.success(
        result.status === 'running'
          ? `第 ${pageNumber} 页已有任务正在生成`
          : `第 ${pageNumber} 页已加入重生成队列`
      );

      const statusPayload = await comicApi.getChapterRegenerationStatus(projectId, chapter.chapter_number);
      updateComicStatusOnly(statusPayload);
      onChapterStatusChange?.();
    } catch (error) {
      delete pendingPageOperationsRef.current[pageNumber];
      console.error('单页重生成失败:', error);
      message.error(`第 ${pageNumber} 页重生成失败`);
    } finally {
      setComicPageRegenerating((prev) => {
        const next = { ...prev };
        delete next[pageNumber];
        return next;
      });
    }
  };

  const handleOpenEditPageModal = (pageNumber: number) => {
    setEditPagePrompt('');
    setEditPageModal({ pageNumber });
  };

  const handleEditComicPage = async () => {
    if (!chapter || !editPageModal) return;
    if (!editPagePrompt.trim()) {
      const { message } = await import('antd');
      message.warning('请输入改图提示词');
      return;
    }
    const { pageNumber } = editPageModal;
    setEditPageModal(null);
    const { message } = await import('antd');

    try {
      setComicPageEditing((prev) => ({ ...prev, [pageNumber]: true }));
      pendingPageOperationsRef.current[pageNumber] = '改图';
      const result = await comicApi.editPage(projectId, chapter.chapter_number, pageNumber, editPagePrompt.trim());
      message.success(result.detail || `第 ${pageNumber} 页改图任务已加入队列`);
      const statusPayload = await comicApi.getChapterRegenerationStatus(projectId, chapter.chapter_number);
      updateComicStatusOnly(statusPayload);
      onChapterStatusChange?.();
    } catch (error) {
      delete pendingPageOperationsRef.current[pageNumber];
      console.error('分镜改图失败:', error);
      message.error(`第 ${pageNumber} 页改图失败`);
    } finally {
      setComicPageEditing((prev) => {
        const next = { ...prev };
        delete next[pageNumber];
        return next;
      });
    }
  };

  const handleGenerateStoryboard = async () => {
    if (!chapter) return;
    const { message } = await import('antd');

    try {
      setStoryboardGenerating(true);
      const result = await comicApi.generateChapterStoryboard(projectId, chapter.chapter_number);
      message.info('分镜生成任务已提交，正在后台处理...');

      const taskId = result.task_id;
      storyboardGenPollRef.current = window.setInterval(async () => {
        try {
          const status = await comicApi.getStoryboardGenerateStatus(projectId, taskId, { silent: true });
          if (status.status === 'completed') {
            if (storyboardGenPollRef.current) {
              window.clearInterval(storyboardGenPollRef.current);
              storyboardGenPollRef.current = null;
            }
            setStoryboardGenerating(false);
            message.success('分镜脚本生成完成');
            await loadComicChapter({ syncEditor: true, silent: true });
            onChapterStatusChange?.();
          } else if (status.status === 'failed') {
            if (storyboardGenPollRef.current) {
              window.clearInterval(storyboardGenPollRef.current);
              storyboardGenPollRef.current = null;
            }
            setStoryboardGenerating(false);
            message.error(`分镜生成失败：${status.error || '未知错误'}`);
          }
        } catch (error) {
          console.warn('轮询分镜生成状态失败，将继续重试:', error);
        }
      }, 3000);
    } catch (error) {
      console.error('提交分镜生成任务失败:', error);
      message.error('提交分镜生成任务失败');
      setStoryboardGenerating(false);
    }
  };

  useEffect(() => {
    return () => {
      if (storyboardGenPollRef.current) {
        window.clearInterval(storyboardGenPollRef.current);
        storyboardGenPollRef.current = null;
      }
    };
  }, []);

  const handleOpenReadMode = () => {
    if (!chapter) return;
    navigate(`/comic-admin/${projectId}?mode=read&chapter=${chapter.chapter_number}`);
  };

  const pageImageUrl = (page: ComicPage) => {
    if (!chapter) {
      return '';
    }
    const imageUrl = page.image_url
      ? buildAppPath(page.image_url)
      : comicApi.getPageImageUrl(projectId, chapter.chapter_number, page.page_number);
    const separator = imageUrl.includes('?') ? '&' : '?';
    return `${imageUrl}${separator}t=${encodeURIComponent(page.updated_at || '')}`;
  };

  const openPagePreview = (page: ComicPage) => {
    const url = pageImageUrl(page);
    if (!url) {
      return;
    }
    setPreviewPage({
      url,
      title: `第 ${page.page_number} 页`,
    });
  };

  const pageErrorMessage = (page: ComicPage) => {
    return getComicPageErrorText(page);
  };

  const renderPageFrame = (page: ComicPage) => {
    const isGenerating = page.status === 'queued' || page.status === 'running';
    const errorText = pageErrorMessage(page);

    if (page.image_available) {
      return (
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <div style={{ position: 'relative' }}>
            <button
              type="button"
              style={{
                appearance: 'none',
                border: 'none',
                padding: 0,
                width: '100%',
                aspectRatio: '720 / 1280',
                display: 'block',
                cursor: 'zoom-in',
                background: 'transparent',
                borderRadius: 8,
                overflow: 'hidden',
              }}
              onClick={() => openPagePreview(page)}
            >
              <img
                src={pageImageUrl(page)}
                alt={`第 ${page.page_number} 页`}
                style={{
                  width: '100%',
                  height: '100%',
                  display: 'block',
                  objectFit: 'cover',
                  borderRadius: 8,
                  border: `1px solid ${token.colorBorderSecondary}`,
                  background: token.colorBgContainer,
                }}
              />
            </button>
            {page.failed && errorText && (
              <Tag color="error" style={{ position: 'absolute', left: 8, top: 8, margin: 0 }}>
                最近失败
              </Tag>
            )}
          </div>
          {page.failed && errorText && (
            <Alert
              type="error"
              showIcon
              message="最近一次操作失败"
              description={compactComicErrorText(errorText)}
            />
          )}
        </Space>
      );
    }

    if (isGenerating) {
      return (
        <div
          style={{
            width: '100%',
            aspectRatio: '720 / 1280',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 8,
            border: `1px solid ${token.colorBorderSecondary}`,
            color: token.colorTextSecondary,
            background: `linear-gradient(135deg, ${token.colorFillTertiary}, ${token.colorFillSecondary})`,
            overflow: 'hidden',
            position: 'relative',
          }}
        >
          <div
            style={{
              position: 'absolute',
              inset: 0,
              background: `linear-gradient(110deg, transparent 0%, ${token.colorFill} 42%, transparent 72%)`,
              transform: 'translateX(-100%)',
              animation: 'comic-page-shimmer 1.4s ease-in-out infinite',
            }}
          />
          <Space direction="vertical" align="center" size={10} style={{ position: 'relative', zIndex: 1 }}>
            <LoadingOutlined style={{ fontSize: 28, color: token.colorPrimary }} />
            <Text strong>{page.status === 'queued' ? '排队生成中' : '漫画页生成中'}</Text>
            <Text type="secondary">第 {page.page_number} 页</Text>
          </Space>
        </div>
      );
    }

    return (
      <div
        style={{
          width: '100%',
          aspectRatio: '720 / 1280',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 8,
          border: `1px dashed ${page.status === 'failed' ? token.colorErrorBorder : token.colorBorder}`,
          color: token.colorTextSecondary,
          background: token.colorBgContainer,
          padding: 18,
          textAlign: 'center',
        }}
      >
        <Space direction="vertical" size={8} style={{ maxWidth: '100%' }}>
          {page.status === 'failed' ? (
            <ExclamationCircleOutlined style={{ fontSize: 28, color: token.colorError }} />
          ) : (
            <PictureOutlined style={{ fontSize: 28, color: token.colorTextTertiary }} />
          )}
          <Text type={page.status === 'failed' ? 'danger' : 'secondary'} strong={page.status === 'failed'}>
            {getComicStatusLabel(page.status)}
          </Text>
          <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {errorText || '暂无图片'}
          </Text>
        </Space>
      </div>
    );
  };

  return (
    <Drawer
      title={chapter ? `第${chapter.chapter_number}章 漫画 / 分镜` : '漫画 / 分镜'}
      open={open}
      onClose={onClose}
      width={mobile ? '100%' : 960}
      destroyOnClose
      extra={(
        <Space wrap>
          <Button
            icon={<ReadOutlined />}
            onClick={handleOpenReadMode}
          >
            看漫画 / 连续阅读
          </Button>
          <Button
            icon={<SyncOutlined />}
            onClick={() => void loadComicChapter({ silent: true })}
            loading={comicLoading}
          >
            刷新
          </Button>
          <Button
            icon={<RobotOutlined />}
            onClick={() => void handleGenerateStoryboard()}
            loading={storyboardGenerating}
          >
            AI生成分镜
          </Button>
          <Button
            icon={<SaveOutlined />}
            onClick={() => void handleSaveStoryboard()}
            loading={comicSaving}
            type="primary"
          >
            保存分镜
          </Button>
          <Button
            icon={<PictureOutlined />}
            onClick={() => void handleRegenerateComicChapter()}
            loading={comicChapterRegenerating}
          >
            生成/重生成本章漫画
          </Button>
        </Space>
      )}
      styles={{
        body: {
          padding: mobile ? 12 : 16,
          background: token.colorBgLayout,
        },
      }}
    >
      <Spin spinning={comicLoading}>
        {!comicData || !chapter ? (
          <Empty description="暂无漫画数据" />
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            {activeStatusSummary && (
              <Alert
                type="info"
                showIcon
                message={`重生成进行中：排队 ${activeStatusSummary.queuedPages} 页，生成中 ${activeStatusSummary.runningPages} 页`}
                description="本页面会自动轮询更新状态，无需等待长时间图片生成请求完成。"
              />
            )}

            <Card size="small">
              <Space wrap>
                <Tag color={getChapterStatusColor(comicData.chapter.status || 'draft')}>
                  章节：{getChapterStatusText(comicData.chapter.status || 'draft')}
                </Tag>
                <Tag color={getComicStatusColor(comicData.storyboard.status || 'missing')}>
                  分镜：{getComicStatusLabel(comicData.storyboard.status || 'missing')}
                </Tag>
                <Tag color={getComicStatusColor(comicData.comic.chapter_status)}>
                  漫画：{getComicStatusLabel(comicData.comic.chapter_status)}
                </Tag>
                <Badge
                  count={`${comicData.comic.available_page_count}/${comicData.comic.page_count} 页`}
                  style={{ backgroundColor: token.colorPrimary }}
                />
              </Space>
            </Card>

            <div
              style={{
                display: 'grid',
                gridTemplateColumns: mobile ? '1fr' : 'minmax(0, 1fr) minmax(0, 1fr)',
                gap: 16,
              }}
            >
              <Card title="章节正文" size="small">
                <TextArea
                  value={comicData.chapter.content || ''}
                  readOnly
                  rows={mobile ? 12 : 20}
                  placeholder="暂无章节正文"
                  style={{ fontFamily: 'monospace', fontSize: mobile ? 12 : 14 }}
                />
              </Card>

              <Card
                title={(
                  <Space wrap>
                    <span>分镜脚本</span>
                    <Radio.Group
                      value={storyboardEditorMode}
                      onChange={(event) => setStoryboardEditorMode(event.target.value)}
                      optionType="button"
                      buttonStyle="solid"
                      size="small"
                    >
                      <Radio.Button value="markdown">Markdown</Radio.Button>
                      <Radio.Button value="json">JSON</Radio.Button>
                    </Radio.Group>
                  </Space>
                )}
                size="small"
              >
                <TextArea
                  value={storyboardEditorMode === 'markdown' ? storyboardMarkdown : storyboardJsonText}
                  onChange={(event) => {
                    if (storyboardEditorMode === 'markdown') {
                      setStoryboardMarkdown(event.target.value);
                    } else {
                      setStoryboardJsonText(event.target.value);
                    }
                  }}
                  rows={mobile ? 12 : 20}
                  placeholder={storyboardEditorMode === 'markdown' ? '编辑分镜 Markdown...' : '编辑分镜 JSON...'}
                  style={{ fontFamily: 'monospace', fontSize: mobile ? 12 : 14 }}
                />
              </Card>
            </div>

            <Card
              title={`漫画图片预览 (${comicData.comic.available_page_count}/${comicData.comic.page_count})`}
              size="small"
            >
              <style>
                {`
                  @keyframes comic-page-shimmer {
                    0% { transform: translateX(-100%); }
                    100% { transform: translateX(100%); }
                  }
                  .comic-page-preview-modal .ant-modal {
                    max-width: none;
                    margin: 0;
                    padding: 0;
                  }
                  .comic-page-preview-modal .ant-modal-content {
                    min-height: 100vh;
                    border-radius: 0;
                    background: rgba(0, 0, 0, 0.94);
                    box-shadow: none;
                  }
                  .comic-page-preview-modal .ant-modal-close {
                    color: #fff;
                    top: 16px;
                    right: 18px;
                    z-index: 2;
                  }
                `}
              </style>
              {comicData.comic.pages.length === 0 ? (
                <Empty description="本章还没有漫画页" />
              ) : (
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: mobile ? '1fr' : 'repeat(2, minmax(0, 1fr))',
                    gap: 16,
                  }}
                >
                  {comicData.comic.pages.map((page) => (
                    <Card
                      key={page.page_number}
                      size="small"
                      title={(
                        <Space wrap>
                          <span>第 {page.page_number} 页</span>
                          <Tag color={getComicStatusColor(page.status)}>{getComicStatusLabel(page.status)}</Tag>
                        </Space>
                      )}
                      extra={(
                        <Space size={4}>
                          {(page.image_available || page.status === 'ready' || page.status === 'completed') && (
                            <Button
                              size="small"
                              onClick={() => handleOpenEditPageModal(page.page_number)}
                              loading={Boolean(comicPageEditing[page.page_number])}
                            >
                              改图
                            </Button>
                          )}
                          <Button
                            size="small"
                            icon={<SyncOutlined />}
                            onClick={() => void handleRegenerateComicPage(page.page_number)}
                            loading={Boolean(comicPageRegenerating[page.page_number])}
                          >
                            单页重生图
                          </Button>
                        </Space>
                      )}
                    >
                      {renderPageFrame(page)}
                    </Card>
                  ))}
                </div>
              )}
            </Card>
          </Space>
        )}
      </Spin>

      <Modal
        open={Boolean(previewPage)}
        footer={null}
        onCancel={() => setPreviewPage(null)}
        title={null}
        width="100vw"
        zIndex={2200}
        getContainer={() => document.body}
        rootClassName="comic-page-preview-modal"
        closeIcon={<CloseOutlined style={{ color: '#fff', fontSize: 18 }} />}
        centered={false}
        styles={{
          body: {
            minHeight: '100vh',
            padding: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'rgba(0, 0, 0, 0.94)',
          },
          content: {
            padding: 0,
            minHeight: '100vh',
            borderRadius: 0,
            background: 'rgba(0, 0, 0, 0.94)',
          },
        }}
        style={{ top: 0, paddingBottom: 0, maxWidth: 'none' }}
      >
        {previewPage && (
          <div
            style={{
              width: '100vw',
              height: '100vh',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              padding: mobile ? '44px 10px 18px' : '48px 32px 24px',
              boxSizing: 'border-box',
            }}
          >
            <img
              src={previewPage.url}
              alt={previewPage.title}
              style={{
                maxWidth: '100%',
                maxHeight: '100%',
                objectFit: 'contain',
                display: 'block',
                background: '#111',
              }}
            />
            <Text
              style={{
                position: 'fixed',
                left: 20,
                top: 18,
                color: '#fff',
                fontSize: 14,
              }}
            >
              {previewPage.title}
            </Text>
          </div>
        )}
      </Modal>

      <Modal
        title={editPageModal ? `改图 - 第 ${editPageModal.pageNumber} 页` : '改图'}
        open={Boolean(editPageModal)}
        onCancel={() => setEditPageModal(null)}
        onOk={handleEditComicPage}
        okText="开始改图"
        cancelText="取消"
        destroyOnClose
        width={480}
      >
        {editPageModal && (() => {
          const targetPage = comicData?.comic.pages.find((p) => p.page_number === editPageModal.pageNumber);
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {targetPage && (targetPage.image_available || targetPage.status === 'ready') && (
                <div style={{ textAlign: 'center' }}>
                  <img
                    src={pageImageUrl(targetPage)}
                    alt={`第 ${editPageModal.pageNumber} 页`}
                    style={{ maxHeight: 200, objectFit: 'contain', borderRadius: 8, border: `1px solid ${token.colorBorderSecondary}` }}
                  />
                </div>
              )}
              <Text type="secondary">输入改图提示词，AI 会基于当前分镜图片进行修改。提示词仅临时使用，不会覆盖分镜内容。</Text>
              <TextArea
                rows={4}
                placeholder="描述你希望改变的部分，如：修改角色表情为微笑、调整背景为夜景、增加光影效果等"
                maxLength={2000}
                showCount
                value={editPagePrompt}
                onChange={(e) => setEditPagePrompt(e.target.value)}
                onPressEnter={(e) => {
                  if (e.ctrlKey || e.metaKey) {
                    handleEditComicPage();
                  }
                }}
              />
            </div>
          );
        })()}
      </Modal>
    </Drawer>
  );
}
