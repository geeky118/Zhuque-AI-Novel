import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { List, Button, Modal, Form, Input, Select, message, Empty, Space, Badge, Tag, Card, InputNumber, Alert, Radio, Descriptions, Collapse, Popconfirm, Pagination, theme, Progress } from 'antd';
import { EditOutlined, FileTextOutlined, ThunderboltOutlined, LockOutlined, DownloadOutlined, SettingOutlined, FundOutlined, SyncOutlined, CheckCircleOutlined, CloseCircleOutlined, RocketOutlined, StopOutlined, InfoCircleOutlined, CaretRightOutlined, DeleteOutlined, BookOutlined, FormOutlined, PlusOutlined, ReadOutlined, PictureOutlined, RobotOutlined } from '@ant-design/icons';
import { useStore } from '../store';
import { useChapterSync } from '../store/hooks';
import { projectApi, writingStyleApi, chapterApi, comicApi } from '../services/api';
import { buildApiPath } from '../utils/basePath';
import type {
  Chapter,
  ChapterUpdate,
  ApiError,
  WritingStyle,
  AnalysisTask,
  ExpansionPlanData,
  ComicBatchGenerateStatusResponse,
  ComicFullPipelineBatchStatusResponse,
  ComicProjectChapterStatus,
  ComicProjectResponse,
  ComicStoryboardState,
  ComicFullPipelineGenerationMode,
} from '../types';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import ChapterAnalysis from '../components/ChapterAnalysis';
import ExpansionPlanEditor from '../components/ExpansionPlanEditor';
import { SSELoadingOverlay } from '../components/SSELoadingOverlay';
import { SSEProgressModal } from '../components/SSEProgressModal';
import ChapterReader from '../components/ChapterReader';
import PartialRegenerateToolbar from '../components/PartialRegenerateToolbar';
import PartialRegenerateModal from '../components/PartialRegenerateModal';
import ComicChapterDrawer from '../components/ComicChapterDrawer';

const { TextArea } = Input;

// localStorage 缓存键名
const WORD_COUNT_CACHE_KEY = 'chapter_default_word_count';
const DEFAULT_WORD_COUNT = 3000;
const ANALYSIS_POLL_INTERVAL_MS = 5000;
const CHAPTER_BATCH_POLL_INTERVAL_MS = 5000;
const COMIC_BATCH_POLL_INTERVAL_MS = 5000;
const FULL_PIPELINE_POLL_INTERVAL_MS = 5000;
const STORYBOARD_BATCH_POLL_INTERVAL_MS = 5000;
const FULL_PIPELINE_HEAVY_REFRESH_INTERVAL_MS = 15000;

const buildPipelineAuxRefreshKey = (status: ComicFullPipelineBatchStatusResponse) => {
  const stageKey = Object.entries(status.stages || {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, stage]) => [
      key,
      stage.processed,
      stage.succeeded,
      stage.failed,
    ].join(':'))
    .join('|');

  return [
    status.status,
    status.current_stage ?? '',
    status.completed,
    status.successful,
    status.failed,
    stageKey,
  ].join('::');
};

const filterChaptersByNumbers = (chapterList: Chapter[], chapterNumbers?: number[] | null) => {
  if (!chapterNumbers || chapterNumbers.length === 0) {
    return chapterList;
  }
  const selected = new Set(chapterNumbers);
  return chapterList.filter((chapter) => selected.has(chapter.chapter_number));
};

const sequentialChapterNumbers = (startChapterNumber?: number | null, count?: number | null) => {
  const start = Number(startChapterNumber || 0);
  const total = Number(count || 0);
  if (!Number.isFinite(start) || !Number.isFinite(total) || start <= 0 || total <= 0) {
    return [];
  }
  return Array.from({ length: total }, (_, index) => start + index);
};

// 从 localStorage 读取缓存的字数
const getCachedWordCount = (): number => {
  try {
    const cached = localStorage.getItem(WORD_COUNT_CACHE_KEY);
    if (cached) {
      const value = parseInt(cached, 10);
      if (!isNaN(value) && value >= 500 && value <= 10000) {
        return value;
      }
    }
  } catch (error) {
    console.warn('读取字数缓存失败:', error);
  }
  return DEFAULT_WORD_COUNT;
};

// 保存字数到 localStorage
const setCachedWordCount = (value: number): void => {
  try {
    localStorage.setItem(WORD_COUNT_CACHE_KEY, String(value));
  } catch (error) {
    console.warn('保存字数缓存失败:', error);
  }
};

const formatWordCount = (value?: number | null) => `${(value || 0).toLocaleString('zh-CN')}字`;

const getStoryboardProgressText = (storyboard?: ComicStoryboardState | null) => {
  const status = storyboard?.status || (storyboard?.exists ? 'available' : 'missing');
  const pageSuffix = typeof storyboard?.page_count === 'number' && storyboard.page_count > 0
    ? ` ${storyboard.page_count}页`
    : '';

  switch (status) {
    case 'available':
    case 'completed':
    case 'ready':
      return `已有${pageSuffix}`;
    case 'edited':
      return `编辑中${pageSuffix}`;
    case 'queued':
      return `排队中${pageSuffix}`;
    case 'running':
      return `生成中${pageSuffix}`;
    case 'failed':
      return `失败${pageSuffix}`;
    default:
      return `缺失${pageSuffix}`;
  }
};

const getComicProgressText = (chapterStatus?: ComicProjectChapterStatus | null) => {
  if (!chapterStatus) {
    return '缺失';
  }

  const totalPages = chapterStatus.page_count || 0;
  const availablePages = chapterStatus.available_page_count || 0;
  const progressText = totalPages > 0 ? ` ${availablePages}/${totalPages}` : '';

  switch (chapterStatus.chapter_status) {
    case 'ready':
    case 'completed':
    case 'partial':
      return `已生成${progressText}`;
    case 'queued':
      return `排队中${progressText}`;
    case 'running':
      return `生成中${progressText}`;
    case 'failed':
      return `失败${progressText}`;
    case 'missing':
      return totalPages > 0 ? `缺失${progressText}` : '缺失';
    default:
      return totalPages > 0 ? `${chapterStatus.chapter_status}${progressText}` : chapterStatus.chapter_status;
  }
};

const PIPELINE_STAGE_ORDER = ['chapter', 'analysis', 'storyboard', 'comic'];
const PIPELINE_STAGE_META: Record<string, { label: string; color: string }> = {
  chapter: { label: '章节', color: 'blue' },
  analysis: { label: '分析', color: 'purple' },
  storyboard: { label: '分镜', color: 'gold' },
  comic: { label: '漫画', color: 'green' },
};

const getPipelineStageLabel = (stage?: string | null) => {
  if (!stage) return '未知';
  return PIPELINE_STAGE_META[stage]?.label || stage;
};

const getPipelineStageItems = (progress?: ComicFullPipelineBatchStatusResponse | null) => {
  const stages = progress?.stages || {};
  const knownKeys = PIPELINE_STAGE_ORDER.filter((key) => stages[key]);
  const extraKeys = Object.keys(stages).filter((key) => !PIPELINE_STAGE_ORDER.includes(key));
  return [...knownKeys, ...extraKeys].map((key) => ({
    key,
    label: getPipelineStageLabel(key),
    color: PIPELINE_STAGE_META[key]?.color || 'default',
    stage: stages[key],
  }));
};

export default function Chapters() {
  const { currentProject, chapters, outlines, setCurrentChapter, setCurrentProject } = useStore();
  const [modal, contextHolder] = Modal.useModal();
  const { token } = theme.useToken();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isEditorOpen, setIsEditorOpen] = useState(false);
  const [isContinuing, setIsContinuing] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form] = Form.useForm();
  const [editorForm] = Form.useForm();
  const [isMobile, setIsMobile] = useState(window.innerWidth <= 768);
  const contentTextAreaRef = useRef<TextAreaRef>(null);
  const [writingStyles, setWritingStyles] = useState<WritingStyle[]>([]);
  const [selectedStyleId, setSelectedStyleId] = useState<number | undefined>();
  const [targetWordCount, setTargetWordCount] = useState<number>(getCachedWordCount);
  const [availableModels, setAvailableModels] = useState<Array<{ value: string, label: string }>>([]);
  const [selectedModel, setSelectedModel] = useState<string | undefined>();
  const [batchSelectedModel, setBatchSelectedModel] = useState<string | undefined>(); // 批量生成的模型选择
  const [temporaryNarrativePerspective, setTemporaryNarrativePerspective] = useState<string | undefined>(); // 临时人称选择
  const [analysisVisible, setAnalysisVisible] = useState(false);
  const [analysisChapterId, setAnalysisChapterId] = useState<string | null>(null);
  // 分析任务状态管理
  const [analysisTasksMap, setAnalysisTasksMap] = useState<Record<string, AnalysisTask>>({});
  const analysisPollingIntervalRef = useRef<number | null>(null);
  const analysisPollingInFlightRef = useRef(false);
  const activeAnalysisPollingIdsRef = useRef<Set<string>>(new Set());

  // 列表查询与分页状态
  const [chapterSearchKeyword, setChapterSearchKeyword] = useState('');
  const [chapterPage, setChapterPage] = useState(1);
  const [chapterPageSize, setChapterPageSize] = useState(20);

  // 阅读器状态
  const [readerVisible, setReaderVisible] = useState(false);
  const [readingChapter, setReadingChapter] = useState<Chapter | null>(null);

  // 规划编辑状态
  const [planEditorVisible, setPlanEditorVisible] = useState(false);
  const [editingPlanChapter, setEditingPlanChapter] = useState<Chapter | null>(null);

  // 局部重写状态
  const [partialRegenerateToolbarVisible, setPartialRegenerateToolbarVisible] = useState(false);
  const [partialRegenerateToolbarPosition, setPartialRegenerateToolbarPosition] = useState({ top: 0, left: 0 });
  const [selectedTextForRegenerate, setSelectedTextForRegenerate] = useState('');
  const [selectionStartPosition, setSelectionStartPosition] = useState(0);
  const [selectionEndPosition, setSelectionEndPosition] = useState(0);
  const [partialRegenerateModalVisible, setPartialRegenerateModalVisible] = useState(false);

  // 单章节生成进度状态
  const [singleChapterProgress, setSingleChapterProgress] = useState(0);
  const [singleChapterProgressMessage, setSingleChapterProgressMessage] = useState('');

  // 批量生成相关状态
  const [batchGenerateVisible, setBatchGenerateVisible] = useState(false);
  const [batchGenerating, setBatchGenerating] = useState(false);
  const [batchAnalyzingUnanalyzed, setBatchAnalyzingUnanalyzed] = useState(false);
  const [batchTaskId, setBatchTaskId] = useState<string | null>(null);
  const [batchForm] = Form.useForm();
  const [manualCreateForm] = Form.useForm();
  const [batchProgress, setBatchProgress] = useState<{
    status: string;
    total: number;
    completed: number;
    current_chapter_number: number | null;
    estimated_time_minutes?: number;
  } | null>(null);
  const batchPollingIntervalRef = useRef<number | null>(null);
  const batchPollingInFlightRef = useRef(false);
  const batchLastAuxRefreshCompletedRef = useRef<number | null>(null);
  const batchTrackedChapterNumbersRef = useRef<number[]>([]);

  // 漫画/分镜管理状态
  const [comicDrawerVisible, setComicDrawerVisible] = useState(false);
  const [comicChapter, setComicChapter] = useState<Chapter | null>(null);
  const [comicProjectData, setComicProjectData] = useState<ComicProjectResponse | null>(null);
  const [batchComicVisible, setBatchComicVisible] = useState(false);
  const [batchComicGenerating, setBatchComicGenerating] = useState(false);
  const [batchComicStartChapter, setBatchComicStartChapter] = useState(1);
  const [batchComicCount, setBatchComicCount] = useState(1);
  const [batchComicConcurrency, setBatchComicConcurrency] = useState(2);
  const [batchComicTaskId, setBatchComicTaskId] = useState<string | null>(null);
  const [batchComicProgress, setBatchComicProgress] = useState<ComicBatchGenerateStatusResponse | null>(null);
  const batchComicPollRef = useRef<number | null>(null);
  const batchComicPollInFlightRef = useRef(false);
  const batchComicLastAuxRefreshCompletedRef = useRef<number | null>(null);
  const [batchPipelineVisible, setBatchPipelineVisible] = useState(false);
  const [batchPipelineGenerating, setBatchPipelineGenerating] = useState(false);
  const [batchPipelineStartChapter, setBatchPipelineStartChapter] = useState(1);
  const [batchPipelineCount, setBatchPipelineCount] = useState(1);
  const [batchPipelineTaskId, setBatchPipelineTaskId] = useState<string | null>(null);
  const [batchPipelineProgress, setBatchPipelineProgress] = useState<ComicFullPipelineBatchStatusResponse | null>(null);
  const batchPipelinePollRef = useRef<number | null>(null);
  const batchPipelinePollInFlightRef = useRef(false);
  const batchPipelineLastAuxRefreshKeyRef = useRef<string | null>(null);
  const batchPipelineLastHeavyRefreshAtRef = useRef(0);
  const [batchPipelineForm] = Form.useForm();
  const [batchStoryboardVisible, setBatchStoryboardVisible] = useState(false);
  const [batchStoryboardGenerating, setBatchStoryboardGenerating] = useState(false);
  const [batchStoryboardProgress, setBatchStoryboardProgress] = useState<{
    status: string;
    total: number;
    completed: number;
    current_chapter_number: number | null;
    errors?: Array<{ chapter_number: number; error: string }>;
  } | null>(null);
  const batchStoryboardPollRef = useRef<number | null>(null);
  const batchStoryboardPollInFlightRef = useRef(false);
  const [batchStoryboardForm] = Form.useForm();
  const chapterProgressSignature = useMemo(
    () => chapters.map((chapter) => `${chapter.id}:${chapter.chapter_number}`).sort().join('|'),
    [chapters]
  );

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth <= 768);
    };

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // 处理文本选中 - 检测选中文本并显示浮动工具栏
  const handleTextSelection = useCallback(() => {
    // 只在编辑器打开时处理选中
    if (!isEditorOpen || isGenerating) {
      setPartialRegenerateToolbarVisible(false);
      return;
    }

    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0) {
      setPartialRegenerateToolbarVisible(false);
      return;
    }

    const selectedText = selection.toString().trim();
    
    // 至少选中10个字符才显示工具栏
    if (selectedText.length < 10) {
      setPartialRegenerateToolbarVisible(false);
      return;
    }

    // 检查选中是否在 TextArea 内
    const textArea = contentTextAreaRef.current?.resizableTextArea?.textArea;
    if (!textArea) {
      setPartialRegenerateToolbarVisible(false);
      return;
    }
    
    // 检查选中是否在 textarea 内（需要特殊处理，因为 textarea 的选中不会创建 range）
    if (document.activeElement !== textArea) {
      setPartialRegenerateToolbarVisible(false);
      return;
    }

    // 获取 textarea 中的选中位置
    const start = textArea.selectionStart;
    const end = textArea.selectionEnd;
    const textContent = textArea.value;
    const selectedInTextArea = textContent.substring(start, end);

    if (selectedInTextArea.trim().length < 10) {
      setPartialRegenerateToolbarVisible(false);
      return;
    }

    // 计算浮动工具栏位置
    const rect = textArea.getBoundingClientRect();
    const computedStyle = window.getComputedStyle(textArea);
    const lineHeight = parseFloat(computedStyle.lineHeight) || 24;
    const paddingTop = parseFloat(computedStyle.paddingTop) || 0;
    
    // 计算选中文本起始位置所在的行号
    const textBeforeSelection = textContent.substring(0, start);
    const startLine = textBeforeSelection.split('\n').length - 1;
    
    // 计算选中文本在 textarea 中的视觉位置
    // 需要考虑 scrollTop（textarea 内部滚动偏移）
    const scrollTop = textArea.scrollTop;
    const visualTop = (startLine * lineHeight) + paddingTop - scrollTop;
    
    // 工具栏位置：textarea 顶部 + 选中文本的视觉位置 - 工具栏高度偏移
    const toolbarTop = rect.top + visualTop - 45;
    
    // 水平位置：放在 textarea 的右侧区域，避免遮挡文本
    const toolbarLeft = rect.right - 180;

    setSelectedTextForRegenerate(selectedInTextArea);
    setSelectionStartPosition(start);
    setSelectionEndPosition(end);
    
    // 计算工具栏位置，如果选中位置不在可视区域内，固定在边缘
    let finalTop = toolbarTop;
    if (visualTop < 0) {
      finalTop = rect.top + 10;
    } else if (visualTop > textArea.clientHeight) {
      finalTop = rect.bottom - 50;
    }
    
    setPartialRegenerateToolbarPosition({
      top: Math.max(rect.top + 10, Math.min(finalTop, rect.bottom - 50)),
      left: Math.min(Math.max(rect.left + 20, toolbarLeft), window.innerWidth - 200),
    });
    setPartialRegenerateToolbarVisible(true);
  }, [isEditorOpen, isGenerating]);

  // 更新工具栏位置的函数（不检测选中，只更新位置）
  const updateToolbarPosition = useCallback(() => {
    if (!partialRegenerateToolbarVisible || !selectedTextForRegenerate) return;
    
    const textArea = contentTextAreaRef.current?.resizableTextArea?.textArea;
    if (!textArea) return;
    
    const rect = textArea.getBoundingClientRect();
    const computedStyle = window.getComputedStyle(textArea);
    const lineHeight = parseFloat(computedStyle.lineHeight) || 24;
    const paddingTop = parseFloat(computedStyle.paddingTop) || 0;
    
    const textContent = textArea.value;
    const textBeforeSelection = textContent.substring(0, selectionStartPosition);
    const startLine = textBeforeSelection.split('\n').length - 1;
    
    const scrollTop = textArea.scrollTop;
    const visualTop = (startLine * lineHeight) + paddingTop - scrollTop;
    
    const toolbarTop = rect.top + visualTop - 45;
    // 固定在 textarea 右上角，不随选中位置变化
    const toolbarLeft = rect.right - 180;
    
    // 工具栏固定在 textarea 可视区域内，即使选中文本滚出视野也保持显示
    // 如果选中位置在可视区域内，跟随选中位置
    // 如果滚出视野，固定在顶部或底部边缘
    let finalTop = toolbarTop;
    if (visualTop < 0) {
      // 选中位置在上方视野外，工具栏固定在顶部
      finalTop = rect.top + 10;
    } else if (visualTop > textArea.clientHeight) {
      // 选中位置在下方视野外，工具栏固定在底部
      finalTop = rect.bottom - 50;
    }
    
    setPartialRegenerateToolbarPosition({
      top: Math.max(rect.top + 10, Math.min(finalTop, rect.bottom - 50)),
      left: Math.min(Math.max(rect.left + 20, toolbarLeft), window.innerWidth - 200),
    });
  }, [partialRegenerateToolbarVisible, selectedTextForRegenerate, selectionStartPosition]);

  // 监听选中事件
  useEffect(() => {
    if (!isEditorOpen) return;

    const textArea = contentTextAreaRef.current?.resizableTextArea?.textArea;
    if (!textArea) return;

    const handleMouseUp = () => {
      // 鼠标释放时检查选中
      setTimeout(handleTextSelection, 50);
    };

    const handleKeyUp = (e: KeyboardEvent) => {
      // Shift + 方向键选中时检查
      if (e.shiftKey && ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) {
        setTimeout(handleTextSelection, 50);
      }
    };

    const handleScroll = () => {
      // 滚动时更新位置（使用 requestAnimationFrame 优化性能）
      requestAnimationFrame(updateToolbarPosition);
    };

    // 监听 textarea 滚动
    textArea.addEventListener('mouseup', handleMouseUp);
    textArea.addEventListener('keyup', handleKeyUp);
    textArea.addEventListener('scroll', handleScroll);

    // 同时监听 Modal body 滚动（Modal 内容可能在外层容器滚动）
    const modalBody = textArea.closest('.ant-modal-body');
    if (modalBody) {
      modalBody.addEventListener('scroll', handleScroll);
    }

    // 监听窗口大小变化
    window.addEventListener('resize', handleScroll);

    return () => {
      textArea.removeEventListener('mouseup', handleMouseUp);
      textArea.removeEventListener('keyup', handleKeyUp);
      textArea.removeEventListener('scroll', handleScroll);
      if (modalBody) {
        modalBody.removeEventListener('scroll', handleScroll);
      }
      window.removeEventListener('resize', handleScroll);
    };
  }, [isEditorOpen, handleTextSelection, updateToolbarPosition]);

  // 点击其他区域时隐藏工具栏
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      
      // 如果点击的是工具栏，不隐藏
      if (target.closest('[data-partial-regenerate-toolbar]')) {
        return;
      }
      
      // 如果点击的是 textarea，不隐藏
      if (target.tagName === 'TEXTAREA') {
        return;
      }
      
      // 如果点击的是 Modal 内部（包括滚动条），不隐藏
      if (target.closest('.ant-modal-content')) {
        return;
      }
      
      // 点击 Modal 外部才隐藏工具栏
      setPartialRegenerateToolbarVisible(false);
    };

    if (partialRegenerateToolbarVisible) {
      document.addEventListener('click', handleClickOutside);
      return () => document.removeEventListener('click', handleClickOutside);
    }
  }, [partialRegenerateToolbarVisible]);

  const {
    refreshChapters,
    updateChapter,
    deleteChapter,
    generateChapterContentStream
  } = useChapterSync();

  useEffect(() => {
    if (currentProject?.id) {
      void refreshChapters(undefined, { silent: true }).then((latestChapters) => {
        void loadAnalysisTasks(latestChapters);
      });
      loadWritingStyles();
      checkAndRestoreBatchTask();
      checkAndRestoreFullPipelineTask();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProject?.id]);

  const loadComicProjectStatuses = useCallback(async (projectId?: string, options?: { silent?: boolean }) => {
    const id = projectId || currentProject?.id;
    if (!id) {
      setComicProjectData(null);
      return null;
    }

    try {
      const response = await comicApi.getProjectChapters(id, { silent: options?.silent });
      setComicProjectData(response);
      return response;
    } catch (error) {
      console.error('加载漫画项目状态失败:', error);
      setComicProjectData(null);
      if (!options?.silent) {
        message.error('加载章节漫画状态失败');
      }
      return null;
    }
  }, [currentProject?.id]);

  useEffect(() => {
    if (!currentProject?.id) {
      setComicProjectData(null);
      return;
    }

    void loadComicProjectStatuses(currentProject.id, { silent: true });
  }, [chapterProgressSignature, currentProject?.id, loadComicProjectStatuses]);

  // 清理轮询定时器
  useEffect(() => {
    return () => {
      if (analysisPollingIntervalRef.current) {
        clearInterval(analysisPollingIntervalRef.current);
        analysisPollingIntervalRef.current = null;
      }
      if (batchPollingIntervalRef.current) {
        clearInterval(batchPollingIntervalRef.current);
        batchPollingIntervalRef.current = null;
      }
      if (batchStoryboardPollRef.current) {
        clearInterval(batchStoryboardPollRef.current);
        batchStoryboardPollRef.current = null;
      }
      if (batchComicPollRef.current) {
        clearInterval(batchComicPollRef.current);
        batchComicPollRef.current = null;
      }
      if (batchPipelinePollRef.current) {
        clearInterval(batchPipelinePollRef.current);
        batchPipelinePollRef.current = null;
      }
    };
  }, []);

  const clearAnalysisPollingIfIdle = useCallback(() => {
    if (activeAnalysisPollingIdsRef.current.size === 0 && analysisPollingIntervalRef.current) {
      clearInterval(analysisPollingIntervalRef.current);
      analysisPollingIntervalRef.current = null;
    }
  }, []);

  const pollActiveAnalysisTasks = useCallback(async () => {
    if (!currentProject?.id) return;
    if (analysisPollingInFlightRef.current) return;

    const activeIds = Array.from(activeAnalysisPollingIdsRef.current);
    if (activeIds.length === 0) {
      clearAnalysisPollingIfIdle();
      return;
    }

    try {
      analysisPollingInFlightRef.current = true;
      const response = await chapterApi.getBatchAnalysisStatuses(currentProject.id, activeIds, { silent: true });
      const tasksMap = response.items || {};

      setAnalysisTasksMap(prev => ({
        ...prev,
        ...tasksMap,
      }));

      activeIds.forEach((chapterId) => {
        const task = tasksMap[chapterId];
        if (!task || task.status === 'completed' || task.status === 'failed' || task.status === 'none') {
          activeAnalysisPollingIdsRef.current.delete(chapterId);

          if (task?.status === 'completed') {
            message.success('章节分析完成');
          } else if (task?.status === 'failed') {
            message.error(`章节分析失败: ${task.error_message || '未知错误'}`);
          }
        }
      });

      clearAnalysisPollingIfIdle();
    } catch (error) {
      console.error('批量轮询分析任务失败:', error);
    } finally {
      analysisPollingInFlightRef.current = false;
    }
  }, [clearAnalysisPollingIfIdle, currentProject?.id]);

  const ensureAnalysisPolling = useCallback(() => {
    if (analysisPollingIntervalRef.current) return;

    analysisPollingIntervalRef.current = window.setInterval(() => {
      void pollActiveAnalysisTasks();
    }, ANALYSIS_POLL_INTERVAL_MS);

    // 立即执行一次
    void pollActiveAnalysisTasks();
  }, [pollActiveAnalysisTasks]);

  // 加载所有章节的分析任务状态（批量接口，避免逐章请求风暴）
  // 接受可选的 chaptersToLoad 参数，解决 React 状态更新延迟导致的问题
  const loadAnalysisTasks = async (chaptersToLoad?: typeof chapters, options?: { silent?: boolean }) => {
    const targetChapters = chaptersToLoad || chapters;
    if (!targetChapters || targetChapters.length === 0 || !currentProject?.id) return;

    const chapterIds = targetChapters
      .filter(chapter => chapter.content && chapter.content.trim() !== '')
      .map(chapter => chapter.id);

    if (chapterIds.length === 0) {
      setAnalysisTasksMap({});
      activeAnalysisPollingIdsRef.current.clear();
      clearAnalysisPollingIfIdle();
      return;
    }

    try {
      const response = await chapterApi.getBatchAnalysisStatuses(currentProject.id, chapterIds, { silent: options?.silent });
      const tasksMap = response.items || {};
      setAnalysisTasksMap(tasksMap);

      activeAnalysisPollingIdsRef.current.clear();
      Object.entries(tasksMap).forEach(([chapterId, task]) => {
        if (task?.status === 'pending' || task?.status === 'running') {
          activeAnalysisPollingIdsRef.current.add(chapterId);
        }
      });

      if (activeAnalysisPollingIdsRef.current.size > 0) {
        ensureAnalysisPolling();
      } else {
        clearAnalysisPollingIfIdle();
      }
    } catch (error) {
      console.error('批量加载分析任务状态失败:', error);
    }
  };

  // 启动单个章节的任务轮询（内部合并到批量轮询）
  const startPollingTask = (chapterId: string) => {
    activeAnalysisPollingIdsRef.current.add(chapterId);
    ensureAnalysisPolling();
  };

  const loadWritingStyles = async () => {
    if (!currentProject?.id) return;

    try {
      const response = await writingStyleApi.getProjectStyles(currentProject.id);
      setWritingStyles(response.styles);

      // 设置默认风格为初始选中
      const defaultStyle = response.styles.find(s => s.is_default);
      if (defaultStyle) {
        setSelectedStyleId(defaultStyle.id);
      }
    } catch (error) {
      console.error('加载写作风格失败:', error);
      message.error('加载写作风格失败');
    }
  };

  const loadAvailableModels = async () => {
    try {
      // 从设置API获取用户配置的模型列表
      const settingsResponse = await fetch(buildApiPath('/settings'));
      if (settingsResponse.ok) {
        const settings = await settingsResponse.json();
        const { api_key, api_base_url, api_provider } = settings;

        if (api_key && api_base_url) {
          try {
            const modelsResponse = await fetch(
              buildApiPath(`/settings/models?api_key=${encodeURIComponent(api_key)}&api_base_url=${encodeURIComponent(api_base_url)}&provider=${api_provider}`)
            );
            if (modelsResponse.ok) {
              const data = await modelsResponse.json();
              if (data.models && data.models.length > 0) {
                setAvailableModels(data.models);
                // 设置默认模型为当前配置的模型
                setSelectedModel(settings.llm_model);
                return settings.llm_model; // 返回模型名称
              }
            }
          } catch {
            console.log('获取模型列表失败，将使用默认模型');
          }
        }
      }
    } catch (error) {
      console.error('加载可用模型失败:', error);
    }
    return null;
  };

  // 检查并恢复批量生成任务
  const checkAndRestoreBatchTask = async () => {
    if (!currentProject?.id) return;

    try {
      const response = await fetch(buildApiPath(`/chapters/project/${currentProject.id}/batch-generate/active`));
      if (!response.ok) return;

      const data = await response.json();

      if (data.has_active_task && data.task) {
        const task = data.task;

        // 恢复任务状态
        setBatchTaskId(task.batch_id);
        setBatchProgress({
          status: task.status,
          total: task.total,
          completed: task.completed,
          current_chapter_number: task.current_chapter_number,
        });
        const estimatedStart = task.current_chapter_number
          ? Math.max(1, Number(task.current_chapter_number) - Number(task.completed || 0))
          : undefined;
        batchTrackedChapterNumbersRef.current = sequentialChapterNumbers(estimatedStart, task.total);
        setBatchGenerating(true);

        // 启动轮询
        startBatchPolling(task.batch_id);
      }
    } catch (error) {
      console.error('检查批量生成任务失败:', error);
    }
  };

  const checkAndRestoreFullPipelineTask = async () => {
    if (!currentProject?.id) return;

    try {
      const response = await fetch(buildApiPath(`/comics/projects/${currentProject.id}/full-batch-generate/active`));
      if (!response.ok) return;

      const data = await response.json();
      if (data.has_active_task && data.task) {
        const task = data.task;
        setBatchPipelineTaskId(task.task_id);
        setBatchPipelineProgress(task as ComicFullPipelineBatchStatusResponse);
        setBatchPipelineGenerating(true);
        startBatchPipelinePolling(task.task_id);
      }
    } catch (error) {
      console.error('检查全流程批量任务失败:', error);
    }
  };

  // 🔔 显示浏览器通知
  const showBrowserNotification = (title: string, body: string, type: 'success' | 'error' | 'info' = 'info') => {
    // 检查浏览器是否支持通知
    if (!('Notification' in window)) {
      console.log('浏览器不支持通知功能');
      return;
    }

    // 检查通知权限
    if (Notification.permission === 'granted') {
      // 选择图标
      const icon = type === 'success' ? '/logo.svg' : type === 'error' ? '/favicon.ico' : '/logo.svg';
      
      const notification = new Notification(title, {
        body,
        icon,
        badge: '/favicon.ico',
        tag: 'batch-generation', // 相同tag会替换旧通知
        requireInteraction: false, // 自动关闭
        silent: false, // 播放提示音
      });

      // 点击通知时聚焦到窗口
      notification.onclick = () => {
        window.focus();
        notification.close();
      };

      // 5秒后自动关闭
      setTimeout(() => {
        notification.close();
      }, 5000);
    } else if (Notification.permission !== 'denied') {
      // 如果权限未被明确拒绝，尝试请求权限
      Notification.requestPermission().then(permission => {
        if (permission === 'granted') {
          showBrowserNotification(title, body, type);
        }
      });
    }
  };

  // 按章节号排序并按大纲分组章节 (必须在早返回之前调用，避免违反 Hooks 规则)
  const { sortedChapters } = useMemo(() => {
    const sorted = [...chapters].sort((a, b) => a.chapter_number - b.chapter_number);

    const groups: Record<string, {
      outlineId: string | null;
      outlineTitle: string;
      outlineOrder: number;
      chapters: Chapter[];
    }> = {};

    sorted.forEach(chapter => {
      const key = chapter.outline_id || 'uncategorized';

      if (!groups[key]) {
        groups[key] = {
          outlineId: chapter.outline_id || null,
          outlineTitle: chapter.outline_title || '未分类章节',
          outlineOrder: chapter.outline_order ?? 999,
          chapters: []
        };
      }

      groups[key].chapters.push(chapter);
    });

    return { sortedChapters: sorted };
  }, [chapters]);

  // 章节查询过滤（前端过滤，减少渲染压力）
  const filteredSortedChapters = useMemo(() => {
    const keyword = chapterSearchKeyword.trim().toLowerCase();
    if (!keyword) return sortedChapters;

    return sortedChapters.filter((chapter) => {
      return (
        String(chapter.chapter_number).includes(keyword) ||
        chapter.title.toLowerCase().includes(keyword) ||
        (chapter.outline_title || '').toLowerCase().includes(keyword)
      );
    });
  }, [sortedChapters, chapterSearchKeyword]);

  // 分页后的扁平章节
  const pagedSortedChapters = useMemo(() => {
    const start = (chapterPage - 1) * chapterPageSize;
    return filteredSortedChapters.slice(start, start + chapterPageSize);
  }, [filteredSortedChapters, chapterPage, chapterPageSize]);

  // one-to-many 模式分页后再按大纲分组
  const pagedGroupedChapters = useMemo(() => {
    const groups: Record<string, {
      outlineId: string | null;
      outlineTitle: string;
      outlineOrder: number;
      chapters: Chapter[];
    }> = {};

    pagedSortedChapters.forEach(chapter => {
      const key = chapter.outline_id || 'uncategorized';
      if (!groups[key]) {
        groups[key] = {
          outlineId: chapter.outline_id || null,
          outlineTitle: chapter.outline_title || '未分类章节',
          outlineOrder: chapter.outline_order ?? 999,
          chapters: []
        };
      }
      groups[key].chapters.push(chapter);
    });

    return Object.values(groups).sort((a, b) => a.outlineOrder - b.outlineOrder);
  }, [pagedSortedChapters]);

  // 搜索词或分页大小变化时重置到第一页
  useEffect(() => {
    setChapterPage(1);
  }, [chapterSearchKeyword, chapterPageSize, currentProject?.outline_mode]);

  // 数据变化导致页码越界时自动纠正
  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(filteredSortedChapters.length / chapterPageSize));
    if (chapterPage > maxPage) {
      setChapterPage(maxPage);
    }
  }, [filteredSortedChapters.length, chapterPage, chapterPageSize]);

  // 预计算每章可生成状态，避免在渲染阶段重复 O(n²) 扫描
  const chapterGenerateGateMap = useMemo(() => {
    const gateMap: Record<string, { canGenerate: boolean; reason: string }> = {};
    const incompleteChapterNumbers: number[] = [];
    const unanalyzedChapters: Array<{ chapterNumber: number; reason: string }> = [];

    sortedChapters.forEach((chapter) => {
      if (incompleteChapterNumbers.length > 0) {
        gateMap[chapter.id] = {
          canGenerate: false,
          reason: `需要先完成前置章节：第 ${incompleteChapterNumbers.join('、')} 章`
        };
      } else if (unanalyzedChapters.length > 0) {
        gateMap[chapter.id] = {
          canGenerate: false,
          reason: `需要先分析前置章节：第 ${unanalyzedChapters.map(c => c.chapterNumber).join('、')} 章 (${unanalyzedChapters.map(c => c.reason).join('、')})`
        };
      } else {
        gateMap[chapter.id] = { canGenerate: true, reason: '' };
      }

      // 将当前章纳入“后续章节”的前置条件
      if (!chapter.content || chapter.content.trim() === '') {
        incompleteChapterNumbers.push(chapter.chapter_number);
      }

      const task = analysisTasksMap[chapter.id];
      if (!task || !task.has_task) {
        unanalyzedChapters.push({ chapterNumber: chapter.chapter_number, reason: '未分析' });
      } else if (task.status === 'pending') {
        unanalyzedChapters.push({ chapterNumber: chapter.chapter_number, reason: '等待分析' });
      } else if (task.status === 'running') {
        unanalyzedChapters.push({ chapterNumber: chapter.chapter_number, reason: '分析中' });
      } else if (task.status === 'failed') {
        unanalyzedChapters.push({ chapterNumber: chapter.chapter_number, reason: '分析失败' });
      } else if (task.status !== 'completed') {
        unanalyzedChapters.push({ chapterNumber: chapter.chapter_number, reason: '状态未知' });
      }
    });

    return gateMap;
  }, [sortedChapters, analysisTasksMap]);

  // 当前可被“一键分析”的章节（有内容且未处于完成/进行中）
  const batchAnalyzableChapterCount = useMemo(() => {
    return sortedChapters.filter((chapter) => {
      if (!chapter.content || chapter.content.trim() === '') return false;
      const task = analysisTasksMap[chapter.id];
      if (!task || !task.has_task) return true;
      return task.status !== 'completed' && task.status !== 'pending' && task.status !== 'running';
    }).length;
  }, [sortedChapters, analysisTasksMap]);

  const comicChapterStatusMap = useMemo(() => {
    return (comicProjectData?.chapters || []).reduce<Record<number, ComicProjectChapterStatus>>((acc, chapter) => {
      acc[chapter.chapter_number] = chapter;
      return acc;
    }, {});
  }, [comicProjectData?.chapters]);

  if (!currentProject) return null;

  // 获取人称的中文显示文本（同时支持中英文值）
  const getNarrativePerspectiveText = (perspective?: string): string => {
    const texts: Record<string, string> = {
      // 英文值映射（向后兼容）
      'first_person': '第一人称（我）',
      'third_person': '第三人称（他/她）',
      'omniscient': '全知视角',
      // 中文值映射（项目设置使用）
      '第一人称': '第一人称（我）',
      '第三人称': '第三人称（他/她）',
      '全知视角': '全知视角',
    };
    return texts[perspective || ''] || '第三人称（默认）';
  };

  const canGenerateChapter = (chapter: Chapter): boolean => {
    return chapterGenerateGateMap[chapter.id]?.canGenerate ?? true;
  };

  const getGenerateDisabledReason = (chapter: Chapter): string => {
    return chapterGenerateGateMap[chapter.id]?.reason || '';
  };

  const handleOpenModal = (id: string) => {
    const chapter = chapters.find(c => c.id === id);
    if (chapter) {
      form.setFieldsValue(chapter);
      setEditingId(id);
      setIsModalOpen(true);
    }
  };

  const handleSubmit = async (values: ChapterUpdate) => {
    if (!editingId) return;

    try {
      await updateChapter(editingId, values);

      // 刷新章节列表以获取完整的章节数据（包括outline_title等联查字段）
      await refreshChapters();

      message.success('章节更新成功');
      setIsModalOpen(false);
      form.resetFields();
    } catch {
      message.error('操作失败');
    }
  };

  const handleOpenEditor = (id: string) => {
    const chapter = chapters.find(c => c.id === id);
    if (chapter) {
      setCurrentChapter(chapter);
      editorForm.setFieldsValue({
        title: chapter.title,
        content: chapter.content,
      });
      setEditingId(id);
      setTemporaryNarrativePerspective(undefined); // 重置人称选择
      setIsEditorOpen(true);
      // 打开编辑窗口时加载模型列表
      loadAvailableModels();
    }
  };

  const handleEditorSubmit = async (values: ChapterUpdate) => {
    if (!editingId || !currentProject) return;

    try {
      await updateChapter(editingId, values);

      // 刷新项目信息以更新总字数统计
      const updatedProject = await projectApi.getProject(currentProject.id);
      setCurrentProject(updatedProject);

      message.success('章节保存成功');
      setIsEditorOpen(false);
    } catch {
      message.error('保存失败');
    }
  };

  const handleGenerate = async () => {
    if (!editingId) return;

    try {
      setIsContinuing(true);
      setIsGenerating(true);
      setSingleChapterProgress(0);
      setSingleChapterProgressMessage('准备开始生成...');

      const result = await generateChapterContentStream(
        editingId,
        (content) => {
          editorForm.setFieldsValue({ content });

          if (contentTextAreaRef.current) {
            const textArea = contentTextAreaRef.current.resizableTextArea?.textArea;
            if (textArea) {
              textArea.scrollTop = textArea.scrollHeight;
            }
          }
        },
        selectedStyleId,
        targetWordCount,
        (progressMsg, progressValue) => {
          // 进度回调
          setSingleChapterProgress(progressValue);
          setSingleChapterProgressMessage(progressMsg);
        },
        selectedModel,  // 传递选中的模型
        temporaryNarrativePerspective  // 传递临时人称参数
      );

      message.success('AI创作成功，正在分析章节内容...');

      // 如果返回了分析任务ID，启动轮询
      if (result?.analysis_task_id) {
        const taskId = result.analysis_task_id;
        setAnalysisTasksMap(prev => ({
          ...prev,
          [editingId]: {
            has_task: true,
            task_id: taskId,
            chapter_id: editingId,
            status: 'pending',
            progress: 0
          }
        }));

        // 启动轮询
        startPollingTask(editingId);
      }
    } catch (error) {
      const apiError = error as ApiError;
      message.error('AI创作失败：' + (apiError.response?.data?.detail || apiError.message || '未知错误'));
    } finally {
      setIsContinuing(false);
      setIsGenerating(false);
      setSingleChapterProgress(0);
      setSingleChapterProgressMessage('');
    }
  };

  const showGenerateModal = (chapter: Chapter) => {
    const previousChapters = chapters.filter(
      c => c.chapter_number < chapter.chapter_number
    ).sort((a, b) => a.chapter_number - b.chapter_number);

    const selectedStyle = writingStyles.find(s => s.id === selectedStyleId);

    const instance = modal.confirm({
      title: 'AI创作章节内容',
      width: 700,
      centered: true,
      content: (
        <div style={{ marginTop: 16 }}>
          <p>AI将根据以下信息创作本章内容：</p>
          <ul>
            <li>章节大纲和要求</li>
            <li>项目的世界观设定</li>
            <li>相关角色信息</li>
            <li><strong>前面已完成章节的内容（确保剧情连贯）</strong></li>
            {selectedStyle && (
              <li><strong>写作风格：{selectedStyle.name}</strong></li>
            )}
            <li><strong>目标字数：{targetWordCount}字</strong></li>
          </ul>

          {previousChapters.length > 0 && (
            <div style={{
              marginTop: 16,
              padding: 12,
              background: token.colorInfoBg,
              borderRadius: token.borderRadius,
              border: `1px solid ${token.colorInfoBorder}`
            }}>
              <div style={{ marginBottom: 8, fontWeight: 500, color: token.colorPrimary }}>
                📚 将引用的前置章节（共{previousChapters.length}章）：
              </div>
              <div style={{ maxHeight: 150, overflowY: 'auto' }}>
                {previousChapters.map(ch => (
                  <div key={ch.id} style={{ padding: '4px 0', fontSize: 13 }}>
                    ✓ 第{ch.chapter_number}章：{ch.title} ({ch.word_count || 0}字)
                  </div>
                ))}
              </div>
              <div style={{ marginTop: 8, fontSize: 12, color: token.colorTextSecondary }}>
                💡 AI会参考这些章节内容，确保情节连贯、角色状态一致
              </div>
            </div>
          )}

          <p style={{ color: token.colorError, marginTop: 16, marginBottom: 0 }}>
            ⚠️ 注意：此操作将覆盖当前章节内容
          </p>
        </div>
      ),
      okText: '开始创作',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        instance.update({
          okButtonProps: { danger: true, loading: true },
          cancelButtonProps: { disabled: true },
          closable: false,
          maskClosable: false,
          keyboard: false,
        });

        try {
          if (!selectedStyleId) {
            message.error('请先选择写作风格');
            instance.update({
              okButtonProps: { danger: true, loading: false },
              cancelButtonProps: { disabled: false },
              closable: true,
              maskClosable: true,
              keyboard: true,
            });
            return;
          }
          await handleGenerate();
          instance.destroy();
        } catch {
          instance.update({
            okButtonProps: { danger: true, loading: false },
            cancelButtonProps: { disabled: false },
            closable: true,
            maskClosable: true,
            keyboard: true,
          });
        }
      },
      onCancel: () => {
        if (isGenerating) {
          message.warning('AI正在创作中，请等待完成');
          return false;
        }
      },
    });
  };

  const getStatusColor = (status: string) => {
    const colors: Record<string, string> = {
      'draft': 'default',
      'writing': 'processing',
      'completed': 'success',
    };
    return colors[status] || 'default';
  };

  const getStatusText = (status: string) => {
    const texts: Record<string, string> = {
      'draft': '草稿',
      'writing': '创作中',
      'completed': '已完成',
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

  const renderChapterProgressGrid = (chapter: Chapter) => {
    const hasContent = Boolean(chapter.content?.trim()) || (chapter.word_count || 0) > 0;
    const comicChapterStatus = comicChapterStatusMap[chapter.chapter_number];
    const progressItems = [
      {
        label: '正文',
        color: hasContent ? 'blue' : 'default',
        text: `${hasContent ? '已写' : '未写'} ${formatWordCount(chapter.word_count)}`,
      },
      {
        label: '分镜',
        color: getComicStatusColor(comicChapterStatus?.storyboard.status || (comicChapterStatus?.storyboard.exists ? 'available' : 'missing')),
        text: getStoryboardProgressText(comicChapterStatus?.storyboard),
      },
      {
        label: '漫画',
        color: getComicStatusColor(comicChapterStatus?.chapter_status || 'missing'),
        text: getComicProgressText(comicChapterStatus),
      },
      {
        label: '完成状态',
        color: getStatusColor(chapter.status),
        text: chapter.status,
      },
    ];

    return (
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: isMobile ? 'repeat(2, minmax(0, 1fr))' : 'repeat(4, minmax(0, 1fr))',
          gap: isMobile ? 8 : 12,
          marginTop: 12,
        }}
      >
        {progressItems.map((item) => (
          <div
            key={`${chapter.id}-${item.label}`}
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              minWidth: 0,
              padding: isMobile ? '8px 10px' : '10px 12px',
              borderRadius: 10,
              border: `1px solid ${token.colorBorderSecondary}`,
              background: token.colorFillAlter,
            }}
          >
            <span style={{ fontSize: 12, color: token.colorTextSecondary, lineHeight: 1.2 }}>
              {item.label}
            </span>
            <Tag
              color={item.color}
              style={{
                margin: 0,
                alignSelf: 'flex-start',
                whiteSpace: 'normal',
                lineHeight: 1.4,
              }}
            >
              {item.text}
            </Tag>
          </div>
        ))}
      </div>
    );
  };

  const renderChapterDescription = (chapter: Chapter) => (
    <>
      {renderChapterProgressGrid(chapter)}
      {chapter.content ? (
        <div style={{ marginTop: 10, color: token.colorTextSecondary, lineHeight: 1.6, fontSize: isMobile ? 12 : 14 }}>
          {chapter.content.substring(0, isMobile ? 80 : 150)}
          {chapter.content.length > (isMobile ? 80 : 150) && '...'}
        </div>
      ) : (
        <div style={{ marginTop: 10, color: token.colorTextTertiary, fontSize: isMobile ? 12 : 14 }}>
          暂无内容
        </div>
      )}
    </>
  );

  const handleOpenComicDrawer = (chapter: Chapter) => {
    setComicChapter(chapter);
    setComicDrawerVisible(true);
  };

  const handleCloseComicDrawer = () => {
    setComicDrawerVisible(false);
    setComicChapter(null);
  };

  const handleExport = () => {
    if (chapters.length === 0) {
      message.warning('当前项目没有章节，无法导出');
      return;
    }

    modal.confirm({
      title: '导出项目章节',
      content: `确定要将《${currentProject.title}》的所有章节导出为TXT文件吗？`,
      centered: true,
      okText: '确定导出',
      cancelText: '取消',
      onOk: () => {
        try {
          projectApi.exportProject(currentProject.id);
          message.success('开始下载导出文件');
        } catch {
          message.error('导出失败，请重试');
        }
      },
    });
  };

  const handleShowAnalysis = (chapterId: string) => {
    setAnalysisChapterId(chapterId);
    setAnalysisVisible(true);
  };

  // 一键按章节顺序分析未分析章节
  const handleBatchAnalyzeUnanalyzed = async () => {
    if (!currentProject?.id) return;

    try {
      setBatchAnalyzingUnanalyzed(true);
      const result = await chapterApi.batchAnalyzeUnanalyzed(currentProject.id);

      if (result.total_started > 0) {
        setAnalysisTasksMap((prev) => ({
          ...prev,
          ...result.started_tasks,
        }));

        Object.keys(result.started_tasks).forEach((chapterId) => {
          startPollingTask(chapterId);
        });

        message.success(
          `已加入 ${result.total_started} 章顺序分析队列（跳过已分析 ${result.total_already_completed} 章，分析中/排队中 ${result.total_skipped_running} 章）`
        );
      } else {
        message.info('没有可启动分析的章节：当前章节要么无内容、要么已分析完成、要么正在分析中');
      }

      if (result.total_started > 0) {
        const startedIds = new Set(Object.keys(result.started_tasks || {}));
        await loadAnalysisTasks(chapters.filter((chapter) => startedIds.has(chapter.id)), { silent: true });
      }
    } catch (error: unknown) {
      const err = error as Error;
      message.error(`一键分析失败：${err.message || '未知错误'}`);
    } finally {
      setBatchAnalyzingUnanalyzed(false);
    }
  };

  // 批量生成函数
  const handleBatchGenerate = async (values: {
    startChapterNumber: number;
    count: number;
    enableAnalysis: boolean;
    styleId?: number;
    targetWordCount?: number;
    model?: string;
  }) => {
    if (!currentProject?.id) return;

    // 调试日志
    console.log('[批量生成] 表单values:', values);
    console.log('[批量生成] batchSelectedModel状态:', batchSelectedModel);

    // 使用批量生成对话框中选择的风格和字数，如果没有选择则使用默认值
    const styleId = values.styleId || selectedStyleId;
    const wordCount = values.targetWordCount || targetWordCount;

    // 使用批量生成专用的模型状态
    const model = batchSelectedModel;

    console.log('[批量生成] 最终使用的model:', model);

    if (!styleId) {
      message.error('请选择写作风格');
      return;
    }

    try {
      setBatchGenerating(true);
      setBatchGenerateVisible(false); // 关闭配置对话框，避免遮挡进度弹窗

      const requestBody: {
        start_chapter_number: number;
        count: number;
        enable_analysis: boolean;
        style_id: number;
        target_word_count: number;
        model?: string;
      } = {
        start_chapter_number: values.startChapterNumber,
        count: values.count,
        enable_analysis: true,
        style_id: styleId,
        target_word_count: wordCount,
      };

      // 如果有模型参数，添加到请求体中
      if (model) {
        requestBody.model = model;
        console.log('[批量生成] 请求体包含model:', model);
      } else {
        console.log('[批量生成] 请求体不包含model，使用后端默认模型');
      }

      console.log('[批量生成] 完整请求体:', JSON.stringify(requestBody, null, 2));

      const response = await fetch(buildApiPath(`/chapters/project/${currentProject.id}/batch-generate`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(requestBody),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '创建批量生成任务失败');
      }

      const result = await response.json();
      batchTrackedChapterNumbersRef.current = (result.chapters_to_generate || [])
        .map((chapter: { chapter_number?: number }) => Number(chapter.chapter_number))
        .filter((chapterNumber: number) => Number.isFinite(chapterNumber) && chapterNumber > 0);
      setBatchTaskId(result.batch_id);
      setBatchProgress({
        status: 'running',
        total: result.chapters_to_generate.length,
        completed: 0,
        current_chapter_number: values.startChapterNumber,
        estimated_time_minutes: result.estimated_time_minutes,
      });

      message.success(`批量生成任务已创建，预计需要 ${result.estimated_time_minutes} 分钟`);

      // 🔔 触发浏览器通知（任务开始）
      showBrowserNotification(
        '批量生成已启动',
        `开始生成 ${result.chapters_to_generate.length} 章，预计需要 ${result.estimated_time_minutes} 分钟`,
        'info'
      );

      // 开始轮询任务状态
      startBatchPolling(result.batch_id);

    } catch (error: unknown) {
      const err = error as Error;
      message.error('创建批量生成任务失败：' + (err.message || '未知错误'));
      setBatchGenerating(false);
      setBatchGenerateVisible(false);
    }
  };

  // 轮询批量生成任务状态
  const startBatchPolling = (taskId: string) => {
    if (batchPollingIntervalRef.current) {
      clearInterval(batchPollingIntervalRef.current);
    }
    batchLastAuxRefreshCompletedRef.current = null;

    const poll = async () => {
      if (batchPollingInFlightRef.current) {
        return;
      }
      try {
        batchPollingInFlightRef.current = true;
        const response = await fetch(buildApiPath(`/chapters/batch-generate/${taskId}/status`));
        if (!response.ok) return;

        const status = await response.json();
        const isTerminal = status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled';
        setBatchProgress({
          status: status.status,
          total: status.total,
          completed: status.completed,
          current_chapter_number: status.current_chapter_number,
        });

        const trackedChapterNumbers = batchTrackedChapterNumbersRef.current;
        // 仅在完成数量变化时刷新重数据，避免长任务期间每轮状态轮询都拉章节和分析状态。
        if (status.completed > 0 && status.completed !== batchLastAuxRefreshCompletedRef.current) {
          batchLastAuxRefreshCompletedRef.current = status.completed;
          const latestChapters = await refreshChapters(undefined, { silent: true });
          const scopedChapters = filterChaptersByNumbers(latestChapters, trackedChapterNumbers);
          await loadAnalysisTasks(scopedChapters, { silent: true });
        }

        // 任务完成或失败，停止轮询
        if (isTerminal) {
          if (batchPollingIntervalRef.current) {
            clearInterval(batchPollingIntervalRef.current);
            batchPollingIntervalRef.current = null;
          }

          setBatchGenerating(false);

          // 立即刷新章节列表和分析任务状态（在显示消息前）
          // 使用 refreshChapters 返回的最新章节列表传递给 loadAnalysisTasks
          const finalChapters = await refreshChapters(undefined, { silent: true });
          await loadAnalysisTasks(filterChaptersByNumbers(finalChapters, trackedChapterNumbers), { silent: true });

          // 刷新项目信息以更新总字数统计
          if (currentProject?.id) {
            const updatedProject = await projectApi.getProject(currentProject.id);
            setCurrentProject(updatedProject);
          }

          if (status.status === 'completed') {
            message.success(`批量生成完成！成功生成 ${status.completed} 章`);
            // 🔔 触发浏览器通知
            showBrowserNotification(
              '批量生成完成',
              `《${currentProject?.title || '项目'}》成功生成 ${status.completed} 章节`,
              'success'
            );
          } else if (status.status === 'failed') {
            message.error(`批量生成失败：${status.error_message || '未知错误'}`);
            // 🔔 触发浏览器通知
            showBrowserNotification(
              '批量生成失败',
              status.error_message || '未知错误',
              'error'
            );
          } else if (status.status === 'cancelled') {
            message.warning('批量生成已取消');
          }

          // 延迟关闭对话框，让用户看到最终状态
          setTimeout(() => {
            setBatchGenerateVisible(false);
            setBatchTaskId(null);
            setBatchProgress(null);
            batchTrackedChapterNumbersRef.current = [];
          }, 2000);
        }
      } catch (error) {
        console.error('轮询批量生成状态失败:', error);
      } finally {
        batchPollingInFlightRef.current = false;
      }
    };

    // 立即执行一次
    poll();

    batchPollingIntervalRef.current = window.setInterval(poll, CHAPTER_BATCH_POLL_INTERVAL_MS);
  };

  // 取消批量生成
  const handleCancelBatchGenerate = async () => {
    if (!batchTaskId) return;

    try {
      const response = await fetch(buildApiPath(`/chapters/batch-generate/${batchTaskId}/cancel`), {
        method: 'POST',
      });

      if (!response.ok) {
        throw new Error('取消失败');
      }

      message.success('批量生成已取消');
      batchTrackedChapterNumbersRef.current = [];

      // 取消后立即刷新章节列表和分析任务，显示已生成的章节
      const latestChapters = await refreshChapters(undefined, { silent: true });
      await loadAnalysisTasks(latestChapters, { silent: true });

      // 刷新项目信息以更新总字数统计
      if (currentProject?.id) {
        const updatedProject = await projectApi.getProject(currentProject.id);
        setCurrentProject(updatedProject);
      }
    } catch (error: unknown) {
      const err = error as Error;
      message.error('取消失败：' + (err.message || '未知错误'));
    }
  };

  // 打开批量生成对话框
  const handleOpenBatchGenerate = async () => {
    // 找到第一个未生成的章节
    const firstIncompleteChapter = sortedChapters.find(
      ch => !ch.content || ch.content.trim() === ''
    );

    if (!firstIncompleteChapter) {
      message.info('所有章节都已生成内容');
      return;
    }

    // 检查该章节是否可以生成
    if (!canGenerateChapter(firstIncompleteChapter)) {
      const reason = getGenerateDisabledReason(firstIncompleteChapter);
      message.warning(reason);
      return;
    }

    // 打开对话框时加载模型列表，等待完成
    const defaultModel = await loadAvailableModels();

    console.log('[打开批量生成] defaultModel:', defaultModel);
    console.log('[打开批量生成] selectedStyleId:', selectedStyleId);

    // 设置批量生成的模型选择状态
    setBatchSelectedModel(defaultModel || undefined);

    // 重置表单并设置初始值（使用缓存的字数）
    batchForm.setFieldsValue({
      startChapterNumber: firstIncompleteChapter.chapter_number,
      count: 5,
      enableAnalysis: false,
      styleId: selectedStyleId,
      targetWordCount: getCachedWordCount(),
    });

    setBatchGenerateVisible(true);
  };

  // 批量生成分镜
  const handleOpenBatchStoryboard = () => {
    const firstChapterWithContent = sortedChapters.find(
      ch => ch.content && ch.content.trim() !== ''
    );
    batchStoryboardForm.setFieldsValue({
      startChapterNumber: firstChapterWithContent?.chapter_number || 1,
      count: 5,
      targetPages: 10,
    });
    setBatchStoryboardVisible(true);
  };

  const handleBatchStoryboardGenerate = async (values: { startChapterNumber: number; count: number; targetPages: number }) => {
    if (!currentProject) return;
    try {
      setBatchStoryboardGenerating(true);
      const result = await comicApi.batchGenerateStoryboard(
        currentProject.id,
        values.startChapterNumber,
        values.count,
        values.targetPages,
      );
      message.success(result.message);
      setBatchStoryboardProgress({ status: 'pending', total: result.total, completed: 0, current_chapter_number: null });

      const pollStoryboardStatus = async () => {
        if (batchStoryboardPollInFlightRef.current) {
          return;
        }
        try {
          batchStoryboardPollInFlightRef.current = true;
          const status = await comicApi.getStoryboardGenerateStatus(currentProject.id, result.task_id, { silent: true });
          setBatchStoryboardProgress({
            status: status.status,
            total: status.total,
            completed: status.completed,
            current_chapter_number: status.current_chapter_number,
            errors: status.errors,
          });
          if (status.status === 'completed' || status.status === 'failed') {
            if (batchStoryboardPollRef.current) {
              window.clearInterval(batchStoryboardPollRef.current);
              batchStoryboardPollRef.current = null;
            }
            setBatchStoryboardGenerating(false);
            if (status.status === 'completed') {
              const errorCount = (status.errors || []).length;
              message.success(`批量分镜生成完成！${errorCount > 0 ? `（${errorCount} 章失败）` : ''}`);
              await loadComicProjectStatuses(currentProject.id, { silent: true });
            } else {
              message.error('批量分镜生成失败');
            }
          }
        } catch (error) {
          console.warn('轮询批量分镜生成状态失败，将继续重试:', error);
        } finally {
          batchStoryboardPollInFlightRef.current = false;
        }
      };
      void pollStoryboardStatus();
      batchStoryboardPollRef.current = window.setInterval(pollStoryboardStatus, STORYBOARD_BATCH_POLL_INTERVAL_MS);
    } catch (error) {
      console.error('批量分镜生成失败:', error);
      message.error('提交批量分镜生成任务失败');
      setBatchStoryboardGenerating(false);
    }
  };

  const stopBatchComicPolling = useCallback(() => {
    if (batchComicPollRef.current) {
      window.clearInterval(batchComicPollRef.current);
      batchComicPollRef.current = null;
    }
  }, []);

  const startBatchComicPolling = useCallback((taskId: string) => {
    if (!currentProject?.id) {
      return;
    }

    stopBatchComicPolling();
    batchComicLastAuxRefreshCompletedRef.current = null;

    const poll = async () => {
      if (!currentProject?.id) {
        return;
      }
      if (batchComicPollInFlightRef.current) {
        return;
      }
      try {
        batchComicPollInFlightRef.current = true;
        const status = await comicApi.getComicBatchGenerateStatus(currentProject.id, taskId, { silent: true });
        setBatchComicProgress(status);
        const isTerminal = status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled';
        if (status.completed !== batchComicLastAuxRefreshCompletedRef.current || isTerminal) {
          batchComicLastAuxRefreshCompletedRef.current = status.completed;
          await loadComicProjectStatuses(currentProject.id, { silent: true });
        }

        if (isTerminal) {
          stopBatchComicPolling();
          setBatchComicGenerating(false);
          const errorCount = status.errors?.length || 0;
          const skippedCount = status.skipped_chapters?.length || 0;
          if (status.status === 'completed' && errorCount === 0) {
            message.success(`批量漫画生成完成${skippedCount > 0 ? `，跳过 ${skippedCount} 章` : ''}`);
          } else if (status.status === 'completed') {
            message.warning(`批量漫画生成完成，但有 ${errorCount} 章处理失败`);
          } else {
            message.error(status.error || '批量漫画生成失败');
          }
        }
      } catch (error) {
        console.error('轮询批量漫画生成状态失败:', error);
      } finally {
        batchComicPollInFlightRef.current = false;
      }
    };

    void poll();
    batchComicPollRef.current = window.setInterval(poll, COMIC_BATCH_POLL_INTERVAL_MS);
  }, [currentProject?.id, loadComicProjectStatuses, stopBatchComicPolling]);

  const handleOpenBatchComic = () => {
    const firstChapterWithStoryboard = comicProjectData?.chapters.find(
      (chapter) => chapter.storyboard.exists || chapter.storyboard.status === 'available' || chapter.storyboard.status === 'ready'
    );
    const firstChapterWithContent = sortedChapters.find(ch => ch.content && ch.content.trim() !== '');
    const startChapterNumber = firstChapterWithStoryboard?.chapter_number || firstChapterWithContent?.chapter_number || 1;
    setBatchComicStartChapter(startChapterNumber);
    setBatchComicCount(1);
    setBatchComicVisible(true);
  };

  const handleBatchComicGenerate = async () => {
    if (!currentProject?.id) return;

    const totalChapters = Math.max(
      ...chapters.map((chapter) => chapter.chapter_number),
      comicProjectData?.summary.chapter_count || 0,
      0,
    );
    if (totalChapters === 0) {
      message.warning('当前项目没有可批量生成的章节');
      return;
    }

    const startChapterNumber = Math.max(1, Math.min(batchComicStartChapter || 1, totalChapters));
    const maxCount = Math.max(1, totalChapters - startChapterNumber + 1);
    const count = Math.max(1, Math.min(batchComicCount || 1, maxCount));

    try {
      setBatchComicGenerating(true);
      const result = await comicApi.batchGenerateComics(currentProject.id, startChapterNumber, count, batchComicConcurrency);
      setBatchComicTaskId(result.task_id);
      setBatchComicProgress({
        task_id: result.task_id,
        project_id: currentProject.id,
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
      message.success(result.message);
      startBatchComicPolling(result.task_id);
    } catch (error) {
      console.error('批量漫画生成失败:', error);
      setBatchComicGenerating(false);
      message.error('提交批量漫画生成任务失败');
    }
  };

  const stopBatchPipelinePolling = useCallback(() => {
    if (batchPipelinePollRef.current) {
      window.clearInterval(batchPipelinePollRef.current);
      batchPipelinePollRef.current = null;
    }
  }, []);

  const startBatchPipelinePolling = useCallback((taskId: string) => {
    if (!currentProject?.id) {
      return;
    }

    stopBatchPipelinePolling();
    batchPipelineLastAuxRefreshKeyRef.current = null;
    batchPipelineLastHeavyRefreshAtRef.current = 0;

    const poll = async () => {
      if (!currentProject?.id) {
        return;
      }
      if (batchPipelinePollInFlightRef.current) {
        return;
      }
      try {
        batchPipelinePollInFlightRef.current = true;
        const status = await comicApi.getFullPipelineBatchGenerateStatus(currentProject.id, taskId, { silent: true });
        setBatchPipelineProgress(status);
        const auxRefreshKey = buildPipelineAuxRefreshKey(status);
        const isTerminal = status.status === 'completed' || status.status === 'failed' || status.status === 'cancelled';
        const now = Date.now();
        const canRunHeavyRefresh = now - batchPipelineLastHeavyRefreshAtRef.current >= FULL_PIPELINE_HEAVY_REFRESH_INTERVAL_MS;
        if (isTerminal || (auxRefreshKey !== batchPipelineLastAuxRefreshKeyRef.current && canRunHeavyRefresh)) {
          batchPipelineLastAuxRefreshKeyRef.current = auxRefreshKey;
          batchPipelineLastHeavyRefreshAtRef.current = now;
          const latestChapters = await refreshChapters(undefined, { silent: true });
          const scopedChapters = filterChaptersByNumbers(latestChapters, status.chapter_numbers);
          const refreshJobs: Array<Promise<unknown>> = [
            loadAnalysisTasks(scopedChapters, { silent: true }),
            loadComicProjectStatuses(currentProject.id, { silent: true }),
          ];
          void Promise.allSettled(refreshJobs).then((results) => {
            results.forEach((result) => {
              if (result.status === 'rejected') {
                console.warn('全流程轮询辅助刷新失败，将继续轮询任务状态:', result.reason);
              }
            });
          });
        }

        if (isTerminal) {
          stopBatchPipelinePolling();
          setBatchPipelineGenerating(false);
          const errorCount = status.errors?.length || 0;
          if (status.status === 'completed' && errorCount === 0) {
            message.success(`全流程批量生成完成${status.failed > 0 ? `，存在 ${status.failed} 个失败记录` : ''}`);
          } else if (status.status === 'completed') {
            message.warning(`全流程批量生成完成，但有 ${errorCount} 条错误记录`);
          } else {
            message.error(status.error_message || '全流程批量生成失败');
          }
        }
      } catch (error) {
        console.error('轮询全流程批量生成状态失败:', error);
      } finally {
        batchPipelinePollInFlightRef.current = false;
      }
    };

    void poll();
    batchPipelinePollRef.current = window.setInterval(poll, FULL_PIPELINE_POLL_INTERVAL_MS);
  }, [currentProject?.id, loadAnalysisTasks, loadComicProjectStatuses, refreshChapters, stopBatchPipelinePolling]);

  const handleOpenBatchPipeline = () => {
    const firstIncompleteChapter = sortedChapters.find(
      ch => !ch.content || ch.content.trim() === ''
    );
    const firstChapterWithContent = sortedChapters.find(
      ch => ch.content && ch.content.trim() !== ''
    );
    const firstChapterWithStoryboard = comicProjectData?.chapters.find(
      (chapter) => chapter.storyboard.exists || chapter.storyboard.status === 'available' || chapter.storyboard.status === 'ready'
    );
    const startChapterNumber = firstIncompleteChapter?.chapter_number
      || firstChapterWithContent?.chapter_number
      || firstChapterWithStoryboard?.chapter_number
      || 1;

    batchPipelineForm.setFieldsValue({
      startChapterNumber: startChapterNumber,
      count: 5,
      styleId: selectedStyleId,
      targetWordCount: getCachedWordCount(),
      enableAnalysis: true,
      model: batchSelectedModel || selectedModel,
      targetPages: 10,
      comicPageConcurrency: 2,
      generationMode: 'incremental',
    });
    setBatchPipelineStartChapter(startChapterNumber);
    setBatchPipelineCount(5);
    setBatchPipelineVisible(true);
  };

  const handleBatchPipelineGenerate = async (values: {
    startChapterNumber: number;
    count: number;
    styleId?: number;
    targetWordCount?: number;
    enableAnalysis?: boolean;
    model?: string;
    targetPages: number;
    comicPageConcurrency?: number;
    generationMode: ComicFullPipelineGenerationMode;
  }) => {
    if (!currentProject?.id) return;

    const totalChapters = Math.max(
      ...chapters.map((chapter) => chapter.chapter_number),
      comicProjectData?.summary.chapter_count || 0,
      0,
    );
    if (totalChapters === 0) {
      message.warning('当前项目没有可批量生成的章节');
      return;
    }

    const startChapterNumber = Math.max(1, Math.min(values.startChapterNumber || 1, totalChapters));
    const maxCount = Math.max(1, totalChapters - startChapterNumber + 1);
    const count = Math.max(1, Math.min(values.count || 1, maxCount));

    try {
      setBatchPipelineGenerating(true);
      const result = await comicApi.batchGenerateFullPipeline(currentProject.id, {
        start_chapter_number: startChapterNumber,
        count,
        style_id: values.styleId,
        target_word_count: values.targetWordCount || getCachedWordCount(),
        enable_analysis: values.enableAnalysis ?? true,
        enable_mcp: true,
        max_retries: 3,
        model: values.model,
        target_pages: values.targetPages || 10,
        comic_page_concurrency: values.comicPageConcurrency || 2,
        generation_mode: values.generationMode || 'incremental',
      });
      const initialStages: ComicFullPipelineBatchStatusResponse['stages'] = {
        chapter: { total: result.total, processed: 0, succeeded: 0, failed: 0 },
        storyboard: { total: result.total, processed: 0, succeeded: 0, failed: 0 },
        comic: { total: result.total, processed: 0, succeeded: 0, failed: 0 },
      };
      if ((values.enableAnalysis ?? true) === true) {
        initialStages.analysis = { total: result.total, processed: 0, succeeded: 0, failed: 0 };
      }
      setBatchPipelineTaskId(result.task_id);
      setBatchPipelineProgress({
        task_id: result.task_id,
        project_id: currentProject.id,
        status: result.status,
        generation_mode: result.generation_mode,
        current_stage: 'chapter',
        total: result.total,
        completed: 0,
        successful: 0,
        failed: 0,
        chapter_numbers: result.chapter_numbers,
        stages: initialStages,
        errors: [],
        chapter_results: [],
      });
      message.success(result.message);
      startBatchPipelinePolling(result.task_id);
    } catch (error) {
      console.error('全流程批量生成失败:', error);
      setBatchPipelineGenerating(false);
      message.error('提交全流程批量任务失败');
    }
  };

  useEffect(() => {
    return () => {
      if (batchStoryboardPollRef.current) {
        window.clearInterval(batchStoryboardPollRef.current);
        batchStoryboardPollRef.current = null;
      }
      stopBatchComicPolling();
      stopBatchPipelinePolling();
    };
  }, [stopBatchComicPolling, stopBatchPipelinePolling]);

  // 手动创建章节(仅one-to-many模式)
  const showManualCreateChapterModal = () => {
    // 计算下一个章节号
    const nextChapterNumber = chapters.length > 0
      ? Math.max(...chapters.map(c => c.chapter_number)) + 1
      : 1;

    modal.confirm({
      title: '手动创建章节',
      width: 600,
      centered: true,
      content: (
        <Form
          form={manualCreateForm}
          layout="vertical"
          initialValues={{
            chapter_number: nextChapterNumber,
            status: 'draft'
          }}
          style={{ marginTop: 16 }}
        >
          <Form.Item
            label="章节序号"
            name="chapter_number"
            rules={[{ required: true, message: '请输入章节序号' }]}
            tooltip="建议按顺序创建章节，确保内容连贯性"
          >
            <InputNumber min={1} style={{ width: '100%' }} placeholder="自动计算的下一个序号" />
          </Form.Item>

          <Form.Item
            label="章节标题"
            name="title"
            rules={[{ required: true, message: '请输入标题' }]}
          >
            <Input placeholder="例如：第一章 初遇" />
          </Form.Item>

          <Form.Item
            label="关联大纲"
            name="outline_id"
            rules={[{ required: true, message: '请选择关联的大纲' }]}
            tooltip="one-to-many模式下，章节必须关联到大纲"
          >
            <Select placeholder="请选择所属大纲">
              {/* 直接使用 store 中的 outlines 数据，而不是从现有章节中提取 */}
              {[...outlines]
                .sort((a, b) => a.order_index - b.order_index)
                .map(outline => (
                  <Select.Option key={outline.id} value={outline.id}>
                    第{outline.order_index}卷：{outline.title}
                  </Select.Option>
                ))}
            </Select>
          </Form.Item>

          <Form.Item
            label="章节摘要（可选）"
            name="summary"
            tooltip="简要描述本章的主要内容和情节发展"
          >
            <TextArea
              rows={4}
              placeholder="简要描述本章内容..."
            />
          </Form.Item>

          <Form.Item
            label="状态"
            name="status"
          >
            <Select>
              <Select.Option value="draft">草稿</Select.Option>
              <Select.Option value="writing">创作中</Select.Option>
              <Select.Option value="completed">已完成</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      ),
      okText: '创建',
      cancelText: '取消',
      onOk: async () => {
        const values = await manualCreateForm.validateFields();

        // 检查章节序号是否已存在
        const conflictChapter = chapters.find(
          ch => ch.chapter_number === values.chapter_number
        );

        if (conflictChapter) {
          // 显示冲突提示Modal
          modal.confirm({
            title: '章节序号冲突',
            icon: <InfoCircleOutlined style={{ color: token.colorError }} />,
            width: 500,
            centered: true,
            content: (
              <div>
                <p style={{ marginBottom: 12 }}>
                  第 <strong>{values.chapter_number}</strong> 章已存在：
                </p>
                <div style={{
                  padding: 12,
                  background: token.colorWarningBg,
                  borderRadius: token.borderRadius,
                  border: `1px solid ${token.colorWarningBorder}`,
                  marginBottom: 12
                }}>
                  <div><strong>标题：</strong>{conflictChapter.title}</div>
                  <div><strong>状态：</strong>{getStatusText(conflictChapter.status)}</div>
                  <div><strong>字数：</strong>{conflictChapter.word_count || 0}字</div>
                  {conflictChapter.outline_title && (
                    <div><strong>所属大纲：</strong>{conflictChapter.outline_title}</div>
                  )}
                </div>
                <p style={{ color: token.colorError, marginBottom: 8 }}>
                  ⚠️ 是否删除旧章节并创建新章节？
                </p>
                <p style={{ fontSize: 12, color: token.colorTextSecondary, marginBottom: 0 }}>
                  删除后将无法恢复，章节内容和分析结果都将被删除。
                </p>
              </div>
            ),
            okText: '删除并创建',
            okButtonProps: { danger: true },
            cancelText: '取消',
            onOk: async () => {
              try {
                // 先删除旧章节
                await handleDeleteChapter(conflictChapter.id);

                // 等待一小段时间确保删除完成
                await new Promise(resolve => setTimeout(resolve, 300));

                // 创建新章节
                await chapterApi.createChapter({
                  project_id: currentProject.id,
                  ...values
                });

                message.success('已删除旧章节并创建新章节');
                await refreshChapters();

                // 刷新项目信息以更新字数统计
                const updatedProject = await projectApi.getProject(currentProject.id);
                setCurrentProject(updatedProject);

                manualCreateForm.resetFields();
              } catch (error: unknown) {
                const err = error as Error;
                message.error('操作失败：' + (err.message || '未知错误'));
                throw error;
              }
            }
          });

          // 阻止外层Modal关闭
          return Promise.reject();
        }

        // 没有冲突，直接创建
        try {
          await chapterApi.createChapter({
            project_id: currentProject.id,
            ...values
          });
          message.success('章节创建成功');
          await refreshChapters();

          // 刷新项目信息以更新字数统计
          const updatedProject = await projectApi.getProject(currentProject.id);
          setCurrentProject(updatedProject);

          manualCreateForm.resetFields();
        } catch (error: unknown) {
          const err = error as Error;
          message.error('创建失败：' + (err.message || '未知错误'));
          throw error;
        }
      }
    });
  };

  // 渲染分析状态标签
  const renderAnalysisStatus = (chapterId: string) => {
    const task = analysisTasksMap[chapterId];

    if (!task) {
      return null;
    }

    switch (task.status) {
      case 'pending':
        return (
          <Tag icon={<SyncOutlined spin />} color="processing">
            等待分析
          </Tag>
        );
      case 'running': {
        // 检查是否正在重试（后端会在error_message中包含"重试"信息）
        const isRetrying = task.error_message && task.error_message.includes('重试');
        return (
          <Tag
            icon={<SyncOutlined spin />}
            color={isRetrying ? "warning" : "processing"}
            title={task.error_message || undefined}
          >
            {isRetrying ? `重试中 ${task.progress}%` : `分析中 ${task.progress}%`}
          </Tag>
        );
      }
      case 'completed':
        return (
          <Tag icon={<CheckCircleOutlined />} color="success">
            已分析
          </Tag>
        );
      case 'failed':
        return (
          <Tag icon={<CloseCircleOutlined />} color="error" title={task.error_message || undefined}>
            分析失败
          </Tag>
        );
      default:
        return null;
    }
  };

  // 显示展开规划详情
  const showExpansionPlanModal = (chapter: Chapter) => {
    if (!chapter.expansion_plan) return;

    try {
      const planData: ExpansionPlanData = JSON.parse(chapter.expansion_plan);

      modal.info({
        title: (
          <Space style={{ flexWrap: 'wrap' }}>
            <InfoCircleOutlined style={{ color: token.colorPrimary }} />
            <span style={{ wordBreak: 'break-word' }}>第{chapter.chapter_number}章展开规划</span>
          </Space>
        ),
        width: isMobile ? 'calc(100vw - 32px)' : 800,
        centered: true,
        style: isMobile ? {
          maxWidth: 'calc(100vw - 32px)',
          margin: '0 auto',
          padding: '0 16px'
        } : undefined,
        styles: {
          body: {
            maxHeight: isMobile ? 'calc(100vh - 200px)' : 'calc(80vh - 110px)',
            overflowY: 'auto'
          }
        },
        content: (
          <div style={{ marginTop: 16 }}>
            <Descriptions
              column={1}
              size="small"
              bordered
              labelStyle={{
                whiteSpace: 'normal',
                wordBreak: 'break-word',
                width: isMobile ? '80px' : '100px'
              }}
              contentStyle={{
                whiteSpace: 'normal',
                wordBreak: 'break-word',
                overflowWrap: 'break-word'
              }}
            >
              <Descriptions.Item label="章节标题">
                <strong style={{
                  wordBreak: 'break-word',
                  whiteSpace: 'normal',
                  overflowWrap: 'break-word'
                }}>
                  {chapter.title}
                </strong>
              </Descriptions.Item>
              <Descriptions.Item label="情感基调">
                <Tag
                  color="blue"
                  style={{
                    whiteSpace: 'normal',
                    wordBreak: 'break-word',
                    height: 'auto',
                    lineHeight: '1.5',
                    padding: '4px 8px'
                  }}
                >
                  {planData.emotional_tone}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="冲突类型">
                <Tag
                  color="orange"
                  style={{
                    whiteSpace: 'normal',
                    wordBreak: 'break-word',
                    height: 'auto',
                    lineHeight: '1.5',
                    padding: '4px 8px'
                  }}
                >
                  {planData.conflict_type}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="预估字数">
                <Tag color="green">{planData.estimated_words}字</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="叙事目标">
                <span style={{
                  wordBreak: 'break-word',
                  whiteSpace: 'normal',
                  overflowWrap: 'break-word'
                }}>
                  {planData.narrative_goal}
                </span>
              </Descriptions.Item>
              <Descriptions.Item label="关键事件">
                <Space direction="vertical" size="small" style={{ width: '100%' }}>
                  {planData.key_events.map((event, idx) => (
                    <div
                      key={idx}
                      style={{
                        padding: '4px 0',
                        wordBreak: 'break-word',
                        whiteSpace: 'normal',
                        overflowWrap: 'break-word'
                      }}
                    >
                      <Tag color="purple" style={{ flexShrink: 0 }}>{idx + 1}</Tag>{' '}
                      <span style={{
                        wordBreak: 'break-word',
                        whiteSpace: 'normal',
                        overflowWrap: 'break-word'
                      }}>
                        {event}
                      </span>
                    </div>
                  ))}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="涉及角色">
                <Space wrap style={{ maxWidth: '100%' }}>
                  {planData.character_focus.map((char, idx) => (
                    <Tag
                      key={idx}
                      color="cyan"
                      style={{
                        whiteSpace: 'normal',
                        wordBreak: 'break-word',
                        height: 'auto',
                        lineHeight: '1.5'
                      }}
                    >
                      {char}
                    </Tag>
                  ))}
                </Space>
              </Descriptions.Item>
              {planData.scenes && planData.scenes.length > 0 && (
                <Descriptions.Item label="场景规划">
                  <Space direction="vertical" size="small" style={{ width: '100%' }}>
                    {planData.scenes.map((scene, idx) => (
                      <Card
                        key={idx}
                        size="small"
                        style={{
                          backgroundColor: token.colorFillQuaternary,
                          maxWidth: '100%',
                          overflow: 'hidden'
                        }}
                      >
                        <div style={{
                          marginBottom: 4,
                          wordBreak: 'break-word',
                          whiteSpace: 'normal',
                          overflowWrap: 'break-word'
                        }}>
                          <strong>📍 地点：</strong>
                          <span style={{
                            wordBreak: 'break-word',
                            whiteSpace: 'normal',
                            overflowWrap: 'break-word'
                          }}>
                            {scene.location}
                          </span>
                        </div>
                        <div style={{ marginBottom: 4 }}>
                          <strong>👥 角色：</strong>
                          <Space
                            size="small"
                            wrap
                            style={{
                              marginLeft: isMobile ? 0 : 8,
                              marginTop: isMobile ? 4 : 0,
                              display: isMobile ? 'flex' : 'inline-flex'
                            }}
                          >
                            {scene.characters.map((char, charIdx) => (
                              <Tag
                                key={charIdx}
                                style={{
                                  whiteSpace: 'normal',
                                  wordBreak: 'break-word',
                                  height: 'auto'
                                }}
                              >
                                {char}
                              </Tag>
                            ))}
                          </Space>
                        </div>
                        <div style={{
                          wordBreak: 'break-word',
                          whiteSpace: 'normal',
                          overflowWrap: 'break-word'
                        }}>
                          <strong>🎯 目的：</strong>
                          <span style={{
                            wordBreak: 'break-word',
                            whiteSpace: 'normal',
                            overflowWrap: 'break-word'
                          }}>
                            {scene.purpose}
                          </span>
                        </div>
                      </Card>
                    ))}
                  </Space>
                </Descriptions.Item>
              )}
            </Descriptions>
            <Alert
              message="提示"
              description="这些是AI在大纲展开时生成的规划信息，可以作为创作章节内容时的参考。"
              type="info"
              showIcon
              style={{ marginTop: 16 }}
            />
          </div>
        ),
        okText: '关闭',
      });
    } catch (error) {
      console.error('解析展开规划失败:', error);
      message.error('展开规划数据格式错误');
    }
  };

  // 删除章节处理函数
  const handleDeleteChapter = async (chapterId: string) => {
    try {
      await deleteChapter(chapterId);

      // 刷新章节列表
      await refreshChapters();

      // 刷新项目信息以更新总字数统计
      if (currentProject) {
        const updatedProject = await projectApi.getProject(currentProject.id);
        setCurrentProject(updatedProject);
      }

      message.success('章节删除成功');
    } catch (error: unknown) {
      const err = error as Error;
      message.error('删除章节失败：' + (err.message || '未知错误'));
    }
  };

  // 打开规划编辑器
  const handleOpenPlanEditor = (chapter: Chapter) => {
    // 直接打开编辑器,如果没有规划数据则创建新的
    setEditingPlanChapter(chapter);
    setPlanEditorVisible(true);
  };

  // 保存规划信息
  const handleSavePlan = async (planData: ExpansionPlanData) => {
    if (!editingPlanChapter) return;

    try {
      const response = await fetch(buildApiPath(`/chapters/${editingPlanChapter.id}/expansion-plan`), {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(planData),
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || '更新失败');
      }

      // 刷新章节列表
      await refreshChapters();

      message.success('规划信息更新成功');

      // 关闭编辑器
      setPlanEditorVisible(false);
      setEditingPlanChapter(null);
    } catch (error: unknown) {
      const err = error as Error;
      message.error('保存规划失败：' + (err.message || '未知错误'));
      throw error;
    }
  };

  // 打开阅读器
  const handleOpenReader = (chapter: Chapter) => {
    setReadingChapter(chapter);
    setReaderVisible(true);
  };

  // 阅读器切换章节
  const handleReaderChapterChange = async (chapterId: string) => {
    try {
      const response = await fetch(buildApiPath(`/chapters/${chapterId}`));
      if (!response.ok) throw new Error('获取章节失败');
      const newChapter = await response.json();
      setReadingChapter(newChapter);
    } catch {
      message.error('加载章节失败');
    }
  };

  // 打开局部重写弹窗
  const handleOpenPartialRegenerate = () => {
    setPartialRegenerateToolbarVisible(false);
    setPartialRegenerateModalVisible(true);
  };

  // 应用局部重写结果
  const handleApplyPartialRegenerate = (newText: string, startPos: number, endPos: number) => {
    // 获取当前内容
    const currentContent = editorForm.getFieldValue('content') || '';
    
    // 替换选中部分
    const newContent = currentContent.substring(0, startPos) + newText + currentContent.substring(endPos);
    
    // 更新表单
    editorForm.setFieldsValue({ content: newContent });
    
    // 关闭弹窗
    setPartialRegenerateModalVisible(false);
    
    message.success('局部重写已应用');
  };

  const pipelineStageItems = getPipelineStageItems(batchPipelineProgress);
  const pipelineStageGridColumns = isMobile
    ? '1fr'
    : `repeat(${Math.max(1, Math.min(pipelineStageItems.length || 1, 4))}, minmax(0, 1fr))`;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, overflow: 'hidden' }}>
      {contextHolder}
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 10,
        backgroundColor: token.colorBgContainer,
        padding: isMobile ? '12px 0' : '16px 0',
        marginBottom: isMobile ? 12 : 16,
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
        display: 'flex',
        flexDirection: isMobile ? 'column' : 'row',
        gap: isMobile ? 12 : 0,
        justifyContent: 'space-between',
        alignItems: isMobile ? 'stretch' : 'center'
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <h2 style={{ margin: 0, fontSize: isMobile ? 18 : 24 }}>
            <BookOutlined style={{ marginRight: 8 }} />
            章节管理
          </h2>
          <Tag
            color={currentProject.outline_mode === 'one-to-one' ? 'blue' : 'green'}
            style={{ width: 'fit-content' }}
          >
            {currentProject.outline_mode === 'one-to-one'
              ? '传统模式：章节由大纲管理，请在大纲页面操作'
              : '细化模式：章节可在大纲页面展开'}
          </Tag>
        </div>
        <Space direction={isMobile ? 'vertical' : 'horizontal'} style={{ width: isMobile ? '100%' : 'auto' }}>
          <Input.Search
            allowClear
            placeholder="搜索章节（序号/标题/大纲）"
            value={chapterSearchKeyword}
            onChange={(e) => setChapterSearchKeyword(e.target.value)}
            style={{ width: isMobile ? '100%' : 280 }}
          />
          {currentProject.outline_mode === 'one-to-many' && (
            <Button
              icon={<PlusOutlined />}
              onClick={showManualCreateChapterModal}
              block={isMobile}
              size={isMobile ? 'middle' : 'middle'}
            >
              手动创建
            </Button>
          )}
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={handleBatchAnalyzeUnanalyzed}
            loading={batchAnalyzingUnanalyzed}
            disabled={chapters.length === 0 || batchAnalyzableChapterCount === 0}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
            style={{ background: token.colorWarning, borderColor: token.colorWarning }}
            title={batchAnalyzableChapterCount === 0 ? '暂无可一键分析章节' : `可一键分析 ${batchAnalyzableChapterCount} 章`}
          >
            一键分析{batchAnalyzableChapterCount > 0 ? ` (${batchAnalyzableChapterCount})` : ''}
          </Button>
          <Button
            type="primary"
            icon={<RocketOutlined />}
            onClick={handleOpenBatchGenerate}
            disabled={chapters.length === 0}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
            style={{ background: token.colorInfo, borderColor: token.colorInfo }}
          >
            批量生成
          </Button>
          <Button
            type="default"
            icon={<PictureOutlined />}
            onClick={handleOpenBatchStoryboard}
            disabled={chapters.length === 0}
            loading={batchStoryboardGenerating}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
          >
            批量分镜
          </Button>
          <Button
            type="default"
            icon={<PictureOutlined />}
            onClick={handleOpenBatchComic}
            disabled={chapters.length === 0}
            loading={batchComicGenerating}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
          >
            批量漫画
          </Button>
          <Button
            type="default"
            icon={<RobotOutlined />}
            onClick={handleOpenBatchPipeline}
            disabled={chapters.length === 0}
            loading={batchPipelineGenerating}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
          >
            全流程批量
          </Button>
          <Button
            type="default"
            icon={<DownloadOutlined />}
            onClick={handleExport}
            disabled={chapters.length === 0}
            block={isMobile}
            size={isMobile ? 'middle' : 'middle'}
          >
            导出为TXT
          </Button>
        </Space>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {chapters.length === 0 ? (
          <Empty description="还没有章节，开始创作吧！" />
        ) : filteredSortedChapters.length === 0 ? (
          <Empty description="未找到匹配章节" />
        ) : currentProject.outline_mode === 'one-to-one' ? (
          // one-to-one 模式：直接显示扁平列表
          <List
            dataSource={pagedSortedChapters}
            renderItem={(item) => (
              <List.Item
                id={`chapter-item-${item.id}`}
                style={{
                  padding: '16px',
                  marginBottom: 16,
                  background: token.colorBgContainer,
                  borderRadius: token.borderRadius,
                  border: `1px solid ${token.colorBorderSecondary}`,
                  flexDirection: isMobile ? 'column' : 'row',
                  alignItems: isMobile ? 'flex-start' : 'center',
                }}
                actions={isMobile ? undefined : [
                  <Button
                    type="text"
                    icon={<ReadOutlined />}
                    onClick={() => handleOpenReader(item)}
                    disabled={!item.content || item.content.trim() === ''}
                    title={!item.content || item.content.trim() === '' ? '暂无内容' : '沉浸式阅读'}
                  >
                    阅读
                  </Button>,
                  <Button
                    type="text"
                    icon={<EditOutlined />}
                    onClick={() => handleOpenEditor(item.id)}
                  >
                    编辑
                  </Button>,
                  <Button
                    type="text"
                    icon={<PictureOutlined />}
                    onClick={() => handleOpenComicDrawer(item)}
                  >
                    漫画
                  </Button>,
                  (() => {
                    const task = analysisTasksMap[item.id];
                    const isAnalyzing = task && (task.status === 'pending' || task.status === 'running');
                    const hasContent = item.content && item.content.trim() !== '';

                    return (
                      <Button
                        type="text"
                        icon={isAnalyzing ? <SyncOutlined spin /> : <FundOutlined />}
                        onClick={() => handleShowAnalysis(item.id)}
                        disabled={!hasContent || isAnalyzing}
                        loading={isAnalyzing}
                        title={
                          !hasContent ? '请先生成章节内容' :
                            isAnalyzing ? '分析进行中，请稍候...' :
                              ''
                        }
                      >
                        {isAnalyzing ? '分析中' : '分析'}
                      </Button>
                    );
                  })(),
                  <Button
                    type="text"
                    icon={<SettingOutlined />}
                    onClick={() => handleOpenModal(item.id)}
                  >
                    修改
                  </Button>,
                ]}
              >
                <div style={{ width: '100%' }}>
                  <List.Item.Meta
                    avatar={!isMobile && <FileTextOutlined style={{ fontSize: 32, color: token.colorPrimary }} />}
                    title={
                      <div style={{
                        display: 'flex',
                        flexDirection: isMobile ? 'column' : 'row',
                        alignItems: isMobile ? 'flex-start' : 'center',
                        gap: isMobile ? 6 : 12,
                        width: '100%'
                      }}>
                        <span style={{ fontSize: isMobile ? 14 : 16, fontWeight: 500, flexShrink: 0 }}>
                          第{item.chapter_number}章：{item.title}
                        </span>
                        <Space wrap size={isMobile ? 4 : 8}>
                          {renderAnalysisStatus(item.id)}
                          {!canGenerateChapter(item) && (
                            <Tag icon={<LockOutlined />} color="warning" title={getGenerateDisabledReason(item)}>
                              需前置章节
                            </Tag>
                          )}
                        </Space>
                      </div>
                    }
                    description={renderChapterDescription(item)}
                  />

                  {isMobile && (
                    <Space style={{ marginTop: 12, width: '100%', justifyContent: 'flex-end' }} wrap>
                      <Button
                        type="text"
                        icon={<ReadOutlined />}
                        onClick={() => handleOpenReader(item)}
                        size="small"
                        disabled={!item.content || item.content.trim() === ''}
                        title={!item.content || item.content.trim() === '' ? '暂无内容' : '阅读'}
                      />
                      <Button
                        type="text"
                        icon={<EditOutlined />}
                        onClick={() => handleOpenEditor(item.id)}
                        size="small"
                        title="编辑"
                      />
                      <Button
                        type="text"
                        icon={<PictureOutlined />}
                        onClick={() => handleOpenComicDrawer(item)}
                        size="small"
                        title="漫画/分镜"
                      />
                      {(() => {
                        const task = analysisTasksMap[item.id];
                        const isAnalyzing = task && (task.status === 'pending' || task.status === 'running');
                        const hasContent = item.content && item.content.trim() !== '';

                        return (
                          <Button
                            type="text"
                            icon={isAnalyzing ? <SyncOutlined spin /> : <FundOutlined />}
                            onClick={() => handleShowAnalysis(item.id)}
                            size="small"
                            disabled={!hasContent || isAnalyzing}
                            loading={isAnalyzing}
                            title={
                              !hasContent ? '请先生成章节内容' :
                                isAnalyzing ? '分析中' :
                                  '分析'
                            }
                          />
                        );
                      })()}
                      <Button
                        type="text"
                        icon={<SettingOutlined />}
                        onClick={() => handleOpenModal(item.id)}
                        size="small"
                        title="修改"
                      />
                    </Space>
                  )}
                </div>
              </List.Item>
            )}
          />
        ) : (
          // one-to-many 模式：按大纲分组显示
          <Collapse
            bordered={false}
            defaultActiveKey={pagedGroupedChapters.length > 0 ? ['0'] : []}
            destroyInactivePanel
            expandIcon={({ isActive }) => <CaretRightOutlined rotate={isActive ? 90 : 0} />}
            style={{ background: 'transparent' }}
          >
            {pagedGroupedChapters.map((group, groupIndex) => (
              <Collapse.Panel
                key={groupIndex.toString()}
                header={
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <Tag color={group.outlineId ? 'blue' : 'default'} style={{ margin: 0 }}>
                      {group.outlineId ? `📖 大纲 ${group.outlineOrder}` : '📝 未分类'}
                    </Tag>
                    <span style={{ fontWeight: 600, fontSize: 16 }}>
                      {group.outlineTitle}
                    </span>
                    <Badge
                      count={`${group.chapters.length} 章`}
                      style={{ backgroundColor: token.colorSuccess }}
                    />
                    <Badge
                      count={`${group.chapters.reduce((sum, ch) => sum + (ch.word_count || 0), 0)} 字`}
                      style={{ backgroundColor: token.colorPrimary }}
                    />
                  </div>
                }
                style={{
                  marginBottom: 16,
                  background: token.colorBgContainer,
                  borderRadius: token.borderRadius,
                  border: `1px solid ${token.colorBorderSecondary}`,
                }}
              >
                <List
                  dataSource={group.chapters}
                  renderItem={(item) => (
                    <List.Item
                      id={`chapter-item-${item.id}`}
                      style={{
                        padding: '16px 0',
                        borderRadius: 8,
                        transition: 'background 0.3s ease',
                        flexDirection: isMobile ? 'column' : 'row',
                        alignItems: isMobile ? 'flex-start' : 'center',
                      }}
                      actions={isMobile ? undefined : [
                        <Button
                          type="text"
                          icon={<ReadOutlined />}
                          onClick={() => handleOpenReader(item)}
                          disabled={!item.content || item.content.trim() === ''}
                          title={!item.content || item.content.trim() === '' ? '暂无内容' : '沉浸式阅读'}
                        >
                          阅读
                        </Button>,
                        <Button
                          type="text"
                          icon={<EditOutlined />}
                          onClick={() => handleOpenEditor(item.id)}
                        >
                          编辑
                        </Button>,
                        <Button
                          type="text"
                          icon={<PictureOutlined />}
                          onClick={() => handleOpenComicDrawer(item)}
                        >
                          漫画
                        </Button>,
                        (() => {
                          const task = analysisTasksMap[item.id];
                          const isAnalyzing = task && (task.status === 'pending' || task.status === 'running');
                          const hasContent = item.content && item.content.trim() !== '';

                          return (
                            <Button
                              type="text"
                              icon={isAnalyzing ? <SyncOutlined spin /> : <FundOutlined />}
                              onClick={() => handleShowAnalysis(item.id)}
                              disabled={!hasContent || isAnalyzing}
                              loading={isAnalyzing}
                              title={
                                !hasContent ? '请先生成章节内容' :
                                  isAnalyzing ? '分析进行中，请稍候...' :
                                    ''
                              }
                            >
                              {isAnalyzing ? '分析中' : '分析'}
                            </Button>
                          );
                        })(),
                        <Button
                          type="text"
                          icon={<SettingOutlined />}
                          onClick={() => handleOpenModal(item.id)}
                        >
                          修改
                        </Button>,
                        // 只在 one-to-many 模式下显示删除按钮
                        ...(currentProject.outline_mode === 'one-to-many' ? [
                          <Popconfirm
                            title="确定删除这个章节吗？"
                            description="删除后将无法恢复，章节内容和分析结果都将被删除。"
                            onConfirm={() => handleDeleteChapter(item.id)}
                            okText="确定删除"
                            cancelText="取消"
                            okButtonProps={{ danger: true }}
                          >
                            <Button
                              type="text"
                              danger
                              icon={<DeleteOutlined />}
                            >
                              删除
                            </Button>
                          </Popconfirm>
                        ] : []),
                      ]}
                    >
                      <div style={{ width: '100%' }}>
                        <List.Item.Meta
                          avatar={!isMobile && <FileTextOutlined style={{ fontSize: 32, color: token.colorPrimary }} />}
                          title={
                            <div style={{
                              display: 'flex',
                              flexDirection: isMobile ? 'column' : 'row',
                              alignItems: isMobile ? 'flex-start' : 'center',
                              gap: isMobile ? 6 : 12,
                              width: '100%'
                            }}>
                                <span style={{ fontSize: isMobile ? 14 : 16, fontWeight: 500, flexShrink: 0 }}>
                                  第{item.chapter_number}章：{item.title}
                                </span>
                                <Space wrap size={isMobile ? 4 : 8}>
                                  {renderAnalysisStatus(item.id)}
                                  {!canGenerateChapter(item) && (
                                    <Tag icon={<LockOutlined />} color="warning" title={getGenerateDisabledReason(item)}>
                                      需前置章节
                                    </Tag>
                                  )}
                                  <Space size={4}>
                                    {item.expansion_plan && (
                                      <InfoCircleOutlined
                                        title="查看展开详情"
                                        style={{ color: token.colorPrimary, cursor: 'pointer', fontSize: 16 }}
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          showExpansionPlanModal(item);
                                        }}
                                      />
                                    )}
                                    <FormOutlined
                                      title={item.expansion_plan ? "编辑规划信息" : "创建规划信息"}
                                      style={{ color: token.colorSuccess, cursor: 'pointer', fontSize: 16 }}
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        handleOpenPlanEditor(item);
                                      }}
                                    />
                                  </Space>
                                </Space>
                              </div>
                            }
                          description={renderChapterDescription(item)}
                        />

                        {isMobile && (
                          <Space style={{ marginTop: 12, width: '100%', justifyContent: 'flex-end' }} wrap>
                            <Button
                              type="text"
                              icon={<ReadOutlined />}
                              onClick={() => handleOpenReader(item)}
                              size="small"
                              disabled={!item.content || item.content.trim() === ''}
                              title={!item.content || item.content.trim() === '' ? '暂无内容' : '阅读'}
                            />
                            <Button
                              type="text"
                              icon={<EditOutlined />}
                              onClick={() => handleOpenEditor(item.id)}
                              size="small"
                              title="编辑"
                            />
                            <Button
                              type="text"
                              icon={<PictureOutlined />}
                              onClick={() => handleOpenComicDrawer(item)}
                              size="small"
                              title="漫画/分镜"
                            />
                            {(() => {
                              const task = analysisTasksMap[item.id];
                              const isAnalyzing = task && (task.status === 'pending' || task.status === 'running');
                              const hasContent = item.content && item.content.trim() !== '';

                              return (
                                <Button
                                  type="text"
                                  icon={isAnalyzing ? <SyncOutlined spin /> : <FundOutlined />}
                                  onClick={() => handleShowAnalysis(item.id)}
                                  size="small"
                                  disabled={!hasContent || isAnalyzing}
                                  loading={isAnalyzing}
                                  title={
                                    !hasContent ? '请先生成章节内容' :
                                      isAnalyzing ? '分析中' :
                                        '分析'
                                  }
                                />
                              );
                            })()}
                            <Button
                              type="text"
                              icon={<SettingOutlined />}
                              onClick={() => handleOpenModal(item.id)}
                              size="small"
                              title="修改"
                            />
                            {/* 只在 one-to-many 模式下显示删除按钮 */}
                            {currentProject.outline_mode === 'one-to-many' && (
                              <Popconfirm
                                title="确定删除？"
                                description="删除后无法恢复"
                                onConfirm={() => handleDeleteChapter(item.id)}
                                okText="删除"
                                cancelText="取消"
                                okButtonProps={{ danger: true }}
                              >
                                <Button
                                  type="text"
                                  danger
                                  icon={<DeleteOutlined />}
                                  size="small"
                                  title="删除章节"
                                />
                              </Popconfirm>
                            )}
                          </Space>
                        )}
                      </div>
                    </List.Item>
                  )}
                />
              </Collapse.Panel>
            ))}
          </Collapse>
        )}
      </div>

      {filteredSortedChapters.length > 0 && (
        <div style={{ paddingTop: 12, display: 'flex', justifyContent: 'flex-end', flexShrink: 0 }}>
          <Pagination
            current={chapterPage}
            pageSize={chapterPageSize}
            total={filteredSortedChapters.length}
            showSizeChanger
            pageSizeOptions={['10', '20', '50', '100']}
            onChange={(page, size) => {
              setChapterPage(page);
              if (size !== chapterPageSize) {
                setChapterPageSize(size);
                setChapterPage(1);
              }
            }}
            showTotal={(total) => `共 ${total} 条`}
            size={isMobile ? 'small' : 'default'}
          />
        </div>
      )}

      <Modal
        title={editingId ? '编辑章节信息' : '添加章节'}
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
        footer={null}
        centered
        width={isMobile ? 'calc(100vw - 32px)' : 520}
        style={isMobile ? {
          maxWidth: 'calc(100vw - 32px)',
          margin: '0 auto',
          padding: '0 16px'
        } : undefined}
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 200px)' : 'calc(80vh - 110px)',
            overflowY: 'auto'
          }
        }}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item
            label="章节标题"
            name="title"
            tooltip={
              currentProject.outline_mode === 'one-to-one'
                ? "章节标题由大纲管理，请在大纲页面修改"
                : "一对多模式下可以修改章节标题"
            }
            rules={
              currentProject.outline_mode === 'one-to-many'
                ? [{ required: true, message: '请输入章节标题' }]
                : undefined
            }
          >
            <Input
              placeholder="输入章节标题"
              disabled={currentProject.outline_mode === 'one-to-one'}
            />
          </Form.Item>

          <Form.Item
            label="章节序号"
            name="chapter_number"
            tooltip="章节序号不允许修改，请删除对应大纲，重新生成"
          >
            <Input type="number" placeholder="章节排序序号" disabled />
          </Form.Item>

          <Form.Item label="状态" name="status">
            <Select placeholder="选择状态">
              <Select.Option value="draft">草稿</Select.Option>
              <Select.Option value="writing">创作中</Select.Option>
              <Select.Option value="completed">已完成</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item>
            <Space style={{ float: 'right' }}>
              <Button onClick={() => setIsModalOpen(false)}>取消</Button>
              <Button type="primary" htmlType="submit">
                更新
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="编辑章节内容"
        open={isEditorOpen}
        onCancel={() => {
          if (isGenerating) {
            message.warning('AI正在创作中，请等待完成后再关闭');
            return;
          }
          setIsEditorOpen(false);
        }}
        closable={!isGenerating}
        maskClosable={false}
        keyboard={!isGenerating}
        width={isMobile ? 'calc(100vw - 32px)' : '85%'}
        centered
        style={isMobile ? {
          maxWidth: 'calc(100vw - 32px)',
          margin: '0 auto',
          padding: '0 16px'
        } : undefined}
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 200px)' : 'calc(100vh - 110px)',
            overflowY: 'auto',
            padding: isMobile ? '16px 12px' : '8px'
          }
        }}
        footer={null}
      >
        <Form form={editorForm} layout="vertical" onFinish={handleEditorSubmit}>
          {/* 章节标题和AI创作按钮 */}
          <Form.Item
            label="章节标题"
            tooltip="（1-1模式请在大纲修改，1-N模式请使用修改按钮编辑）"
            style={{ marginBottom: isMobile ? 16 : 12 }}
          >
            <Space.Compact style={{ width: '100%' }}>
              <Form.Item name="title" noStyle>
                <Input disabled style={{ flex: 1 }} />
              </Form.Item>
              {editingId && (() => {
                const currentChapter = chapters.find(c => c.id === editingId);
                const canGenerate = currentChapter ? canGenerateChapter(currentChapter) : false;
                const disabledReason = currentChapter ? getGenerateDisabledReason(currentChapter) : '';

                return (
                  <Button
                    type="primary"
                    icon={canGenerate ? <ThunderboltOutlined /> : <LockOutlined />}
                    onClick={() => currentChapter && showGenerateModal(currentChapter)}
                    loading={isContinuing}
                    disabled={!canGenerate}
                    danger={!canGenerate}
                    style={{ fontWeight: 'bold' }}
                    title={!canGenerate ? disabledReason : '根据大纲和前置章节内容创作'}
                  >
                    {isMobile ? 'AI' : 'AI创作'}
                  </Button>
                );
              })()}
            </Space.Compact>
          </Form.Item>

          {/* 第一行：写作风格 + 叙事角度 */}
          <div style={{
            display: isMobile ? 'block' : 'flex',
            gap: isMobile ? 0 : 16,
            marginBottom: isMobile ? 0 : 12
          }}>
            <Form.Item
              label="写作风格"
              tooltip="选择AI创作时使用的写作风格"
              required
              style={{ flex: 1, marginBottom: isMobile ? 16 : 0 }}
            >
              <Select
                placeholder="请选择写作风格"
                value={selectedStyleId}
                onChange={setSelectedStyleId}
                disabled={isGenerating}
                status={!selectedStyleId ? 'error' : undefined}
              >
                {writingStyles.map(style => (
                  <Select.Option key={style.id} value={style.id}>
                    {style.name}{style.is_default && ' (默认)'}
                  </Select.Option>
                ))}
              </Select>
              {!selectedStyleId && (
                <div style={{ color: token.colorError, fontSize: 12, marginTop: 4 }}>请选择写作风格</div>
              )}
            </Form.Item>

            <Form.Item
              label="叙事角度"
              tooltip="第一人称(我)代入感强；第三人称(他/她)更客观；全知视角洞悉一切"
              style={{ flex: 1, marginBottom: isMobile ? 16 : 0 }}
            >
              <Select
                placeholder={`项目默认: ${getNarrativePerspectiveText(currentProject?.narrative_perspective)}`}
                value={temporaryNarrativePerspective}
                onChange={setTemporaryNarrativePerspective}
                allowClear
                disabled={isGenerating}
              >
                <Select.Option value="第一人称">第一人称(我)</Select.Option>
                <Select.Option value="第三人称">第三人称(他/她)</Select.Option>
                <Select.Option value="全知视角">全知视角</Select.Option>
              </Select>
              {temporaryNarrativePerspective && (
                <div style={{ color: token.colorSuccess, fontSize: 12, marginTop: 4 }}>
                  ✓ {getNarrativePerspectiveText(temporaryNarrativePerspective)}
                </div>
              )}
            </Form.Item>
          </div>

          {/* 第二行：目标字数 + AI模型 */}
          <div style={{
            display: isMobile ? 'block' : 'flex',
            gap: isMobile ? 0 : 16,
            marginBottom: isMobile ? 16 : 12
          }}>
            <Form.Item
              label="目标字数"
              tooltip="AI生成章节时的目标字数，实际可能略有偏差（修改后会自动记住）"
              style={{ flex: 1, marginBottom: isMobile ? 16 : 0 }}
            >
              <InputNumber
                min={500}
                max={10000}
                step={100}
                value={targetWordCount}
                onChange={(value) => {
                  const newValue = value || DEFAULT_WORD_COUNT;
                  setTargetWordCount(newValue);
                  setCachedWordCount(newValue);
                }}
                disabled={isGenerating}
                style={{ width: '100%' }}
                formatter={(value) => `${value} 字`}
                parser={(value) => parseInt(value?.replace(' 字', '') || '0', 10) as unknown as 500}
              />
            </Form.Item>

            <Form.Item
              label="AI模型"
              tooltip="选择用于生成章节内容的AI模型，不选择则使用默认模型"
              style={{ flex: 1, marginBottom: isMobile ? 16 : 0 }}
            >
              <Select
                placeholder={selectedModel ? `默认: ${availableModels.find(m => m.value === selectedModel)?.label || selectedModel}` : "使用默认模型"}
                value={selectedModel}
                onChange={setSelectedModel}
                allowClear
                disabled={isGenerating}
                showSearch
                optionFilterProp="label"
              >
                {availableModels.map(model => (
                  <Select.Option key={model.value} value={model.value} label={model.label}>
                    {model.label}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
          </div>

          <Form.Item label="章节内容" name="content">
            <TextArea
              ref={contentTextAreaRef}
              rows={isMobile ? 12 : 20}
              placeholder="开始写作..."
              style={{ fontFamily: 'monospace', fontSize: isMobile ? 12 : 14 }}
              disabled={isGenerating}
            />
          </Form.Item>

          {/* 局部重写浮动工具栏 */}
          <div data-partial-regenerate-toolbar>
            <PartialRegenerateToolbar
              visible={partialRegenerateToolbarVisible && !isGenerating}
              position={partialRegenerateToolbarPosition}
              selectedText={selectedTextForRegenerate}
              onRegenerate={handleOpenPartialRegenerate}
            />
          </div>

          <Form.Item>
            <Space style={{ width: '100%', justifyContent: 'flex-end', flexDirection: isMobile ? 'column' : 'row', alignItems: isMobile ? 'stretch' : 'center' }}>
              <Space style={{ width: isMobile ? '100%' : 'auto' }}>
                <Button
                  onClick={() => {
                    if (isGenerating) {
                      message.warning('AI正在创作中，请等待完成后再关闭');
                      return;
                    }
                    setIsEditorOpen(false);
                  }}
                  block={isMobile}
                  disabled={isGenerating}
                >
                  取消
                </Button>
                <Button
                  type="primary"
                  htmlType="submit"
                  block={isMobile}
                  disabled={isGenerating}
                >
                  保存章节
                </Button>
              </Space>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {currentProject?.id && (
        <ComicChapterDrawer
          projectId={currentProject.id}
          chapter={comicChapter}
          open={comicDrawerVisible}
          onClose={handleCloseComicDrawer}
          mobile={isMobile}
          onChapterStatusChange={() => {
            void loadComicProjectStatuses(currentProject.id, { silent: true });
          }}
        />
      )}

      {analysisChapterId && (
        <ChapterAnalysis
          chapterId={analysisChapterId}
          visible={analysisVisible}
          onClose={() => {
            setAnalysisVisible(false);

            void refreshChapters(undefined, { silent: true }).then((latestChapters) => {
              void loadAnalysisTasks(latestChapters, { silent: true });
            });

            // 刷新项目信息以更新字数统计
            if (currentProject) {
              projectApi.getProject(currentProject.id)
                .then(updatedProject => {
                  setCurrentProject(updatedProject);
                })
                .catch(error => {
                  console.error('刷新项目信息失败:', error);
                });
            }

            setAnalysisChapterId(null);
          }}
        />
      )}

      {/* 批量生成对话框 */}
      <Modal
        title={
          <Space>
            <RocketOutlined style={{ color: token.colorInfo }} />
            <span>批量生成章节内容</span>
          </Space>
        }
        open={batchGenerateVisible}
        onCancel={() => {
          if (batchGenerating) {
            modal.confirm({
              title: '确认取消',
              content: '批量生成正在进行中，确定要取消吗？',
              okText: '确定取消',
              cancelText: '继续生成',
              centered: true,
              onOk: () => {
                handleCancelBatchGenerate();
                setBatchGenerateVisible(false);
              },
            });
          } else {
            setBatchGenerateVisible(false);
          }
        }}
        footer={!batchGenerating ? (
          <Space style={{ width: '100%', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            <Button onClick={() => setBatchGenerateVisible(false)}>
              取消
            </Button>
            <Button type="primary" icon={<RocketOutlined />} onClick={() => batchForm.submit()}>
              开始批量生成
            </Button>
          </Space>
        ) : null}
        width={isMobile ? 'calc(100vw - 32px)' : 700}
        centered
        closable={!batchGenerating}
        maskClosable={!batchGenerating}
        style={isMobile ? {
          maxWidth: 'calc(100vw - 32px)',
          margin: '0 auto',
          padding: '0 16px'
        } : undefined}
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 200px)' : 'calc(100vh - 260px)',
            overflowY: 'auto',
            overflowX: 'hidden'
          }
        }}
      >
        {!batchGenerating ? (
          <Form
            form={batchForm}
            layout="vertical"
            onFinish={handleBatchGenerate}
            initialValues={{
              startChapterNumber: sortedChapters.find(ch => !ch.content || ch.content.trim() === '')?.chapter_number || 1,
              count: 5,
              enableAnalysis: true,
              styleId: selectedStyleId,
              targetWordCount: getCachedWordCount(),
              model: selectedModel,
            }}
          >
            <Alert
              message="批量生成说明：严格按序生成 | 统一风格字数 | 任一失败则终止"
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            {/* 第一行：起始章节 + 生成数量 */}
            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 0 : 16 }}>
              <Form.Item
                label="起始章节"
                name="startChapterNumber"
                rules={[{ required: true, message: '请选择' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Select placeholder="选择起始章节">
                  {sortedChapters
                    .filter(ch => !ch.content || ch.content.trim() === '')
                    .filter(ch => canGenerateChapter(ch))
                    .map(ch => (
                      <Select.Option key={ch.id} value={ch.chapter_number}>
                        第{ch.chapter_number}章：{ch.title}
                      </Select.Option>
                    ))}
                </Select>
              </Form.Item>

              <Form.Item
                label="生成数量"
                name="count"
                rules={[
                  { required: true, message: '请输入生成数量' },
                  { type: 'number', min: 1, message: '生成数量至少为 1 章' },
                ]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <InputNumber
                  min={1}
                  max={Math.max(chapters.length || 1, comicProjectData?.summary.chapter_count || 1)}
                  step={1}
                  precision={0}
                  style={{ width: '100%' }}
                  addonAfter="章"
                  onChange={(value) => {
                    if (typeof value === 'number' && value > 0) {
                      setBatchPipelineCount(value);
                    }
                  }}
                />
              </Form.Item>
            </div>

            {/* 第二行：写作风格 + 目标字数 */}
            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 0 : 16 }}>
              <Form.Item
                label="写作风格"
                name="styleId"
                rules={[{ required: true, message: '请选择' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Select placeholder="请选择写作风格" showSearch optionFilterProp="children">
                  {writingStyles.map(style => (
                    <Select.Option key={style.id} value={style.id}>
                      {style.name}{style.is_default && ' (默认)'}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>

              <Form.Item
                label="目标字数"
                name="targetWordCount"
                rules={[{ required: true, message: '请设置' }]}
                tooltip="修改后自动记住"
                style={{ flex: 1, marginBottom: 12 }}
              >
                <InputNumber
                  min={500}
                  max={10000}
                  step={100}
                  style={{ width: '100%' }}
                  formatter={(value) => `${value} 字`}
                  parser={(value) => parseInt(value?.replace(' 字', '') || '0', 10) as unknown as 500}
                  onChange={(value) => {
                    if (value) {
                      setCachedWordCount(value);
                    }
                  }}
                />
              </Form.Item>
            </div>

            {/* 第三行：AI模型 + 同步分析 */}
            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 0 : 16 }}>
              <Form.Item
                label="AI模型"
                tooltip="不选则使用默认模型"
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Select
                  placeholder={batchSelectedModel ? `默认: ${availableModels.find(m => m.value === batchSelectedModel)?.label || batchSelectedModel}` : "使用默认模型"}
                  value={batchSelectedModel}
                  onChange={setBatchSelectedModel}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                >
                  {availableModels.map(model => (
                    <Select.Option key={model.value} value={model.value} label={model.label}>
                      {model.label}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>

              <Form.Item
                label="同步分析"
                name="enableAnalysis"
                tooltip="必须开启，确保剧情连贯"
                style={{ marginBottom: 12 }}
              >
                <Radio.Group disabled>
                  <Radio value={true}>
                    <span style={{ fontSize: 12, color: token.colorSuccess }}>✓ 自动更新角色状态</span>
                  </Radio>
                </Radio.Group>
              </Form.Item>
            </div>
          </Form>
        ) : (
          <div>
            <Alert
              message="温馨提示"
              description={
                <ul style={{ margin: '8px 0 0 0', paddingLeft: 20 }}>
                  <li>批量生成需要一定时间，可以切换到其他页面</li>
                  <li>关闭页面后重新打开，会自动恢复任务进度</li>
                  <li>可以随时点击"取消任务"按钮中止生成</li>
                  {batchProgress?.estimated_time_minutes && batchProgress.completed === 0 && (
                    <li>⏱️ 预计耗时：约 {batchProgress.estimated_time_minutes} 分钟</li>
                  )}
                </ul>
              }
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <div style={{ textAlign: 'center' }}>
              <Button
                danger
                icon={<StopOutlined />}
                onClick={() => {
                  modal.confirm({
                    title: '确认取消',
                    content: '确定要取消批量生成吗？已生成的章节将保留。',
                    okText: '确定取消',
                    cancelText: '继续生成',
                    okButtonProps: { danger: true },
                    onOk: handleCancelBatchGenerate,
                  });
                }}
              >
                取消任务
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {/* 单章节生成进度显示 */}
      <SSELoadingOverlay
        loading={isGenerating}
        progress={singleChapterProgress}
        message={singleChapterProgressMessage}
      />

      {/* 批量生成进度显示 - 使用统一的进度组件 */}
      <SSEProgressModal
        visible={batchGenerating}
        progress={batchProgress ? Math.round((batchProgress.completed / batchProgress.total) * 100) : 0}
        message={
          batchProgress?.current_chapter_number
            ? `正在生成第 ${batchProgress.current_chapter_number} 章... (${batchProgress.completed}/${batchProgress.total})`
            : `批量生成进行中... (${batchProgress?.completed || 0}/${batchProgress?.total || 0})`
        }
        title="批量生成章节"
        onCancel={() => {
          modal.confirm({
            title: '确认取消',
            content: '确定要取消批量生成吗？已生成的章节将保留。',
            okText: '确定取消',
            cancelText: '继续生成',
            okButtonProps: { danger: true },
            centered: true,
            onOk: handleCancelBatchGenerate,
          });
        }}
        cancelButtonText="取消任务"
      />

      {/* 全流程批量生成对话框 */}
      <Modal
        title={
          <Space>
            <RobotOutlined style={{ color: token.colorPrimary }} />
            <span>全流程批量生成</span>
          </Space>
        }
        open={batchPipelineVisible}
        onCancel={() => {
          if (!batchPipelineGenerating) {
            setBatchPipelineVisible(false);
          }
        }}
        footer={!batchPipelineGenerating ? (
          <Space style={{ width: '100%', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            <Button onClick={() => setBatchPipelineVisible(false)}>取消</Button>
            <Button type="primary" icon={<RobotOutlined />} onClick={() => batchPipelineForm.submit()}>
              开始全流程生成
            </Button>
          </Space>
        ) : null}
        width={isMobile ? 'calc(100vw - 32px)' : 760}
        centered
        closable={!batchPipelineGenerating}
        maskClosable={!batchPipelineGenerating}
      >
        {!batchPipelineGenerating ? (
          <Form
            form={batchPipelineForm}
            layout="vertical"
            onFinish={handleBatchPipelineGenerate}
            initialValues={{
              startChapterNumber: batchPipelineStartChapter,
              count: batchPipelineCount,
              styleId: selectedStyleId,
              targetWordCount: getCachedWordCount(),
              enableAnalysis: true,
              model: batchSelectedModel || selectedModel,
              targetPages: 10,
              comicPageConcurrency: 2,
              generationMode: 'incremental',
            }}
          >
            {batchPipelineProgress && (
              <Alert
                type={batchPipelineProgress.failed > 0 || (batchPipelineProgress.errors?.length || 0) > 0 ? 'warning' : 'success'}
                showIcon
                style={{ marginBottom: 16 }}
                message={
                  batchPipelineProgress.status === 'completed'
                    ? '全流程批量生成已完成'
                    : `最近一次任务：${batchPipelineProgress.status}`
                }
                description={(
                  <Space direction="vertical" size={4}>
                    <span style={{ color: token.colorTextSecondary }}>
                      进度 {batchPipelineProgress.completed}/{batchPipelineProgress.total}
                    </span>
                    <span style={{ color: token.colorTextSecondary }}>
                      成功 {batchPipelineProgress.successful} · 失败 {batchPipelineProgress.failed}
                    </span>
                    {batchPipelineProgress.generation_mode && (
                      <span style={{ color: token.colorTextSecondary }}>
                        模式 {batchPipelineProgress.generation_mode === 'incremental' ? '增量补充' : '完整重建'}
                      </span>
                    )}
                  </Space>
                )}
              />
            )}
            <Alert
              type="info"
              showIcon
              message="会按章节顺序依次执行章节生成、同步分析、分镜生成和漫画生成，单章失败会记录后继续后续章节。"
              style={{ marginBottom: 16 }}
            />
            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 0 : 16 }}>
              <Form.Item
                label="起始章节"
                name="startChapterNumber"
                rules={[{ required: true, message: '请选择' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Select placeholder="选择起始章节">
                  {sortedChapters.map((ch) => (
                    <Select.Option key={ch.id} value={ch.chapter_number}>
                      第{ch.chapter_number}章：{ch.title}
                      {ch.content && ch.content.trim() ? '（已有内容）' : '（待生成）'}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>

              <Form.Item
                label="生成数量"
                name="count"
                rules={[
                  { required: true, message: '请输入生成数量' },
                  { type: 'number', min: 1, message: '生成数量至少为 1 章' },
                ]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <InputNumber
                  min={1}
                  max={Math.max(chapters.length || 1, comicProjectData?.summary.chapter_count || 1)}
                  step={1}
                  precision={0}
                  style={{ width: '100%' }}
                  addonAfter="章"
                  onChange={(value) => {
                    if (typeof value === 'number' && value > 0) {
                      setBatchPipelineCount(value);
                    }
                  }}
                />
              </Form.Item>
            </div>

            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 0 : 16 }}>
              <Form.Item
                label="写作风格"
                name="styleId"
                rules={[{ required: true, message: '请选择' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Select placeholder="请选择写作风格" showSearch optionFilterProp="children">
                  {writingStyles.map(style => (
                    <Select.Option key={style.id} value={style.id}>
                      {style.name}{style.is_default && ' (默认)'}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>

              <Form.Item
                label="目标字数"
                name="targetWordCount"
                rules={[{ required: true, message: '请设置' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <InputNumber
                  min={500}
                  max={10000}
                  step={100}
                  style={{ width: '100%' }}
                  formatter={(value) => `${value} 字`}
                  parser={(value) => parseInt(value?.replace(' 字', '') || '0', 10) as unknown as 500}
                  onChange={(value) => {
                    if (value) {
                      setCachedWordCount(value);
                    }
                  }}
                />
              </Form.Item>
            </div>

            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 0 : 16 }}>
              <Form.Item
                label="目标分镜页数"
                name="targetPages"
                rules={[{ required: true, message: '请设置' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <InputNumber min={4} max={30} step={1} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item
                label="漫画页并发"
                name="comicPageConcurrency"
                rules={[{ required: true, message: '请设置' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <InputNumber min={1} max={6} step={1} precision={0} style={{ width: '100%' }} addonAfter="页" />
              </Form.Item>

              <Form.Item
                label="生成模式"
                name="generationMode"
                rules={[{ required: true, message: '请选择生成模式' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Radio.Group buttonStyle="solid">
                  <Radio.Button value="incremental">增量补充</Radio.Button>
                  <Radio.Button value="full">完整重建</Radio.Button>
                </Radio.Group>
              </Form.Item>

              <Form.Item
                label="AI模型"
                name="model"
                tooltip="不选则使用默认模型"
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Select
                  placeholder={batchSelectedModel ? `默认: ${availableModels.find(m => m.value === batchSelectedModel)?.label || batchSelectedModel}` : '使用默认模型'}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                >
                  {availableModels.map(model => (
                    <Select.Option key={model.value} value={model.value} label={model.label}>
                      {model.label}
                    </Select.Option>
                  ))}
                </Select>
              </Form.Item>
            </div>

            <Form.Item
              label="同步分析"
              name="enableAnalysis"
              tooltip="开启后会在章节生成后自动执行分析"
              style={{ marginBottom: 0 }}
            >
              <Radio.Group buttonStyle="solid">
                <Radio.Button value={true}>开启</Radio.Button>
                <Radio.Button value={false}>关闭</Radio.Button>
              </Radio.Group>
            </Form.Item>
          </Form>
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message={
                batchPipelineProgress?.current_stage
                  ? `当前阶段：${getPipelineStageLabel(batchPipelineProgress.current_stage)}`
                  : '全流程批量生成正在执行'
              }
              description={
                <Space direction="vertical" size={4}>
                  {batchPipelineTaskId && (
                    <span style={{ color: token.colorTextSecondary }}>
                      任务 {batchPipelineTaskId.slice(0, 8)}
                    </span>
                  )}
                  {batchPipelineProgress?.generation_mode && (
                    <span style={{ color: token.colorTextSecondary }}>
                      模式 {batchPipelineProgress.generation_mode === 'incremental' ? '增量补充' : '完整重建'}
                    </span>
                  )}
                  <span style={{ color: token.colorTextSecondary }}>
                    进度 {batchPipelineProgress?.completed || 0}/{batchPipelineProgress?.total || 0}
                  </span>
                  <span style={{ color: token.colorTextSecondary }}>
                    成功 {batchPipelineProgress?.successful || 0} · 失败 {batchPipelineProgress?.failed || 0}
                  </span>
                  {batchPipelineProgress?.errors && batchPipelineProgress.errors.length > 0 && (
                    <span style={{ color: token.colorError }}>
                      错误记录 {batchPipelineProgress.errors.length} 条
                    </span>
                  )}
                </Space>
              }
            />
            <div style={{ display: 'grid', gap: 8, gridTemplateColumns: pipelineStageGridColumns }}>
              {pipelineStageItems.map((item) => (
                <Tag key={item.key} color={item.color}>
                  {item.label} {item.stage?.processed || 0}/{item.stage?.total || 0}
                </Tag>
              ))}
            </div>
            <Progress
              percent={
                batchPipelineProgress && batchPipelineProgress.total > 0
                  ? Math.round((batchPipelineProgress.completed / batchPipelineProgress.total) * 100)
                  : 0
              }
              status="active"
            />
          </Space>
        )}
      </Modal>

      {/* 批量漫画生成对话框 */}
      <Modal
        title={
          <Space>
            <PictureOutlined style={{ color: token.colorPrimary }} />
            <span>批量生成漫画</span>
          </Space>
        }
        open={batchComicVisible}
        onCancel={() => {
          if (!batchComicGenerating) {
            setBatchComicVisible(false);
          }
        }}
        footer={!batchComicGenerating ? (
          <Space style={{ width: '100%', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            <Button onClick={() => setBatchComicVisible(false)}>取消</Button>
            <Button type="primary" icon={<PictureOutlined />} onClick={() => void handleBatchComicGenerate()}>
              开始生成漫画
            </Button>
          </Space>
        ) : null}
        width={isMobile ? 'calc(100vw - 32px)' : 640}
        centered
        closable={!batchComicGenerating}
        maskClosable={!batchComicGenerating}
      >
        {!batchComicGenerating ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message="会在后台按章节顺序生成漫画图片，已接入失败重试和提示词安全改写。"
            />
            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 16 }}>
              <Space direction="vertical" size={4} style={{ flex: 1 }}>
                <span>起始章节</span>
                <InputNumber
                  min={1}
                  max={Math.max(...chapters.map((chapter) => chapter.chapter_number), 1)}
                  value={batchComicStartChapter}
                  onChange={(value) => setBatchComicStartChapter(value || 1)}
                  style={{ width: '100%' }}
                />
              </Space>
              <Space direction="vertical" size={4} style={{ flex: 1 }}>
                <span>章节数量</span>
                <InputNumber
                  min={1}
                  max={Math.max(chapters.length, 1)}
                  value={batchComicCount}
                  onChange={(value) => setBatchComicCount(value || 1)}
                  style={{ width: '100%' }}
                />
              </Space>
              <Space direction="vertical" size={4} style={{ flex: 1 }}>
                <span>漫画页并发</span>
                <InputNumber
                  min={1}
                  max={6}
                  precision={0}
                  value={batchComicConcurrency}
                  onChange={(value) => setBatchComicConcurrency(value || 2)}
                  style={{ width: '100%' }}
                  addonAfter="页"
                />
              </Space>
            </div>
            {batchComicProgress?.errors && batchComicProgress.errors.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message="上次批量漫画生成存在失败章节"
                description={batchComicProgress.errors.slice(0, 3).map((errorItem) => (
                  <div key={errorItem.chapter_number}>
                    第 {errorItem.chapter_number} 章：{errorItem.error}
                  </div>
                ))}
              />
            )}
          </Space>
        ) : (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Progress
              percent={batchComicProgress?.total ? Math.round((batchComicProgress.completed / batchComicProgress.total) * 100) : 0}
              status={batchComicProgress?.status === 'failed' ? 'exception' : 'active'}
            />
            <Alert
              type={batchComicProgress?.status === 'failed' ? 'error' : 'info'}
              showIcon
              message={
                batchComicProgress?.current_chapter_number
                  ? `正在生成第 ${batchComicProgress.current_chapter_number} 章漫画... (${batchComicProgress.completed}/${batchComicProgress.total})`
                  : `准备中... (${batchComicProgress?.completed || 0}/${batchComicProgress?.total || 0})`
              }
              description={batchComicTaskId ? `后台任务：${batchComicTaskId}` : undefined}
            />
            {batchComicProgress?.errors && batchComicProgress.errors.length > 0 && (
              <Alert
                type="warning"
                showIcon
                message="已有失败记录，任务会继续处理后续章节"
                description={batchComicProgress.errors.slice(0, 5).map((errorItem) => (
                  <div key={errorItem.chapter_number}>
                    第 {errorItem.chapter_number} 章：{errorItem.error}
                  </div>
                ))}
              />
            )}
          </Space>
        )}
      </Modal>

      {/* 批量分镜生成对话框 */}
      <Modal
        title={
          <Space>
            <RobotOutlined style={{ color: token.colorInfo }} />
            <span>批量生成分镜脚本</span>
          </Space>
        }
        open={batchStoryboardVisible}
        onCancel={() => {
          if (!batchStoryboardGenerating) {
            setBatchStoryboardVisible(false);
          }
        }}
        footer={!batchStoryboardGenerating ? (
          <Space style={{ width: '100%', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
            <Button onClick={() => setBatchStoryboardVisible(false)}>取消</Button>
            <Button type="primary" icon={<RobotOutlined />} onClick={() => batchStoryboardForm.submit()}>
              开始批量生成
            </Button>
          </Space>
        ) : null}
        width={isMobile ? 'calc(100vw - 32px)' : 520}
        centered
        closable={!batchStoryboardGenerating}
        maskClosable={!batchStoryboardGenerating}
      >
        {!batchStoryboardGenerating ? (
          <Form
            form={batchStoryboardForm}
            layout="vertical"
            onFinish={handleBatchStoryboardGenerate}
          >
            <Alert
              message="将为选定范围的章节自动生成分镜脚本，需要章节已有正文内容"
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
            />

            <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: isMobile ? 0 : 16 }}>
              <Form.Item
                label="起始章节"
                name="startChapterNumber"
                rules={[{ required: true, message: '请选择' }]}
                style={{ flex: 1, marginBottom: 12 }}
              >
                <Select placeholder="选择起始章节">
                  {sortedChapters
                    .filter(ch => ch.content && ch.content.trim() !== '')
                    .map(ch => (
                      <Select.Option key={ch.id} value={ch.chapter_number}>
                        第{ch.chapter_number}章：{ch.title}
                      </Select.Option>
                    ))}
                </Select>
              </Form.Item>

              <Form.Item
                label="生成数量"
                name="count"
                rules={[{ required: true, message: '请输入' }]}
                style={{ marginBottom: 12 }}
              >
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
            </div>

            <Form.Item
              label="目标分镜页数（每章）"
              name="targetPages"
              tooltip="AI 将尝试为每章生成接近此数量的分镜页"
              style={{ marginBottom: 0 }}
            >
              <InputNumber min={4} max={30} style={{ width: 120 }} />
            </Form.Item>
          </Form>
        ) : (
          <div>
            <div style={{ textAlign: 'center', marginBottom: 16 }}>
              <Progress
                percent={batchStoryboardProgress ? Math.round((batchStoryboardProgress.completed / batchStoryboardProgress.total) * 100) : 0}
                status={batchStoryboardProgress?.status === 'failed' ? 'exception' : 'active'}
              />
              <div style={{ marginTop: 8, color: token.colorTextSecondary }}>
                {batchStoryboardProgress?.current_chapter_number
                  ? `正在生成第 ${batchStoryboardProgress.current_chapter_number} 章分镜... (${batchStoryboardProgress.completed}/${batchStoryboardProgress.total})`
                  : `准备中... (${batchStoryboardProgress?.completed || 0}/${batchStoryboardProgress?.total || 0})`
                }
              </div>
            </div>
            {batchStoryboardProgress?.errors && batchStoryboardProgress.errors.length > 0 && (
              <Alert
                message="部分章节生成失败"
                description={batchStoryboardProgress.errors.map((errorItem, i) => (
                  <div key={i}>
                    第 {errorItem.chapter_number} 章：{errorItem.error}
                  </div>
                ))}
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
              />
            )}
          </div>
        )}
      </Modal>

      {/* 章节阅读器 */}
      {readingChapter && (
        <ChapterReader
          visible={readerVisible}
          chapter={readingChapter}
          onClose={() => {
            setReaderVisible(false);
            setReadingChapter(null);
          }}
          onChapterChange={handleReaderChapterChange}
        />
      )}

      {/* 局部重写弹窗 */}
      {editingId && (
        <PartialRegenerateModal
          visible={partialRegenerateModalVisible}
          chapterId={editingId}
          selectedText={selectedTextForRegenerate}
          startPosition={selectionStartPosition}
          endPosition={selectionEndPosition}
          styleId={selectedStyleId}
          onClose={() => setPartialRegenerateModalVisible(false)}
          onApply={handleApplyPartialRegenerate}
        />
      )}

      {/* 规划编辑器 */}
      {editingPlanChapter && currentProject && (() => {
        let parsedPlanData = null;
        try {
          if (editingPlanChapter.expansion_plan) {
            parsedPlanData = JSON.parse(editingPlanChapter.expansion_plan);
          }
        } catch (error) {
          console.error('解析规划数据失败:', error);
        }

        return (
          <ExpansionPlanEditor
            visible={planEditorVisible}
            planData={parsedPlanData}
            chapterSummary={editingPlanChapter.summary || null}
            projectId={currentProject.id}
            onSave={handleSavePlan}
            onCancel={() => {
              setPlanEditorVisible(false);
              setEditingPlanChapter(null);
            }}
          />
        );
      })()}
    </div>
  );
}
