import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Col, Divider, Empty, Form, Image, Input, InputNumber, Modal, Popconfirm, Row, Select, Space, Spin, Tabs, Tag, Typography, message, theme } from 'antd';
import { BookOutlined, DownloadOutlined, ExportOutlined, ImportOutlined, PictureOutlined, PlusOutlined, ReloadOutlined, TeamOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { useStore } from '../store';
import { useCharacterSync } from '../store/hooks';
import { charactersPageGridConfig } from '../components/CardStyles';
import { CharacterCard } from '../components/CharacterCard';
import { SSELoadingOverlay } from '../components/SSELoadingOverlay';
import type {
  ApiError,
  Character,
  CharacterImageActionResponse,
  CharacterImageState,
  CharacterImageVariantCreateRequest,
  CharacterImageVariantType,
} from '../types';
import { characterApi, characterImageApi } from '../services/api';
import { SSEPostClient } from '../utils/sseClient';
import { buildApiPath, buildAppPath } from '../utils/basePath';
import api from '../services/api';

const { Text, Title } = Typography;
const { TextArea } = Input;

interface Career {
  id: string;
  name: string;
  type: 'main' | 'sub';
  max_stage: number;
}

// 副职业数据类型
interface SubCareerData {
  career_id: string;
  stage: number;
}

// 角色创建表单值类型
interface CharacterFormValues {
  name: string;
  age?: string;
  gender?: string;
  role_type?: string;
  personality?: string;
  appearance?: string;
  background?: string;
  main_career_id?: string;
  main_career_stage?: number;
  sub_career_data?: SubCareerData[];
  // 组织字段
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
}

// 角色创建数据类型
interface CharacterCreateData {
  project_id: string;
  name: string;
  is_organization: boolean;
  age?: string;
  gender?: string;
  role_type?: string;
  personality?: string;
  appearance?: string;
  background?: string;
  main_career_id?: string;
  main_career_stage?: number;
  sub_careers?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
}

// 角色更新数据类型
interface CharacterUpdateData {
  name?: string;
  age?: string;
  gender?: string;
  role_type?: string;
  personality?: string;
  appearance?: string;
  background?: string;
  main_career_id?: string;
  main_career_stage?: number;
  sub_careers?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
}

const IMAGE_STATUS_TEXT: Record<string, string> = {
  none: '未生成',
  generating: '生成中',
  ready: '已生成',
  capacity: '接口繁忙',
  policy: '提示词需调整',
  failed: '生成失败',
};

const IMAGE_STATUS_COLOR: Record<string, string> = {
  none: 'default',
  generating: 'processing',
  ready: 'success',
  capacity: 'warning',
  policy: 'orange',
  failed: 'error',
};

const DEFAULT_VARIANT_KEY = 'default';
const IMAGE_VARIANT_TYPE_TEXT: Record<string, string> = {
  default: '默认形象',
  period: '时期/阶段',
  volume: '分卷',
};

const formatVariantRange = (variant?: Pick<CharacterImageState, 'chapter_start' | 'chapter_end'> | null) => {
  if (!variant) return null;
  if (variant.chapter_start != null && variant.chapter_end != null) {
    return `第 ${variant.chapter_start}-${variant.chapter_end} 章`;
  }
  if (variant.chapter_start != null) {
    return `第 ${variant.chapter_start} 章起`;
  }
  if (variant.chapter_end != null) {
    return `至第 ${variant.chapter_end} 章`;
  }
  return null;
};

export default function Characters() {
  const { token } = theme.useToken();
  const { currentProject, characters } = useStore();
  const [isGenerating, setIsGenerating] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState('');
  const [activeTab, setActiveTab] = useState<'all' | 'character' | 'organization'>('all');
  const [generateForm] = Form.useForm();
  const [generateOrgForm] = Form.useForm();
  const [createForm] = Form.useForm();
  const [editForm] = Form.useForm();
  const [imageVariantForm] = Form.useForm();
  const [createVariantForm] = Form.useForm();
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [createType, setCreateType] = useState<'character' | 'organization'>('character');
  const [editingCharacter, setEditingCharacter] = useState<Character | null>(null);
  const [mainCareers, setMainCareers] = useState<Career[]>([]);
  const [subCareers, setSubCareers] = useState<Career[]>([]);
  const [selectedCharacters, setSelectedCharacters] = useState<string[]>([]);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const [characterImageStates, setCharacterImageStates] = useState<Record<string, CharacterImageState>>({});
  const [isCharacterImageModalOpen, setIsCharacterImageModalOpen] = useState(false);
  const [imageModalCharacter, setImageModalCharacter] = useState<Character | null>(null);
  const [currentImageVariants, setCurrentImageVariants] = useState<CharacterImageState[]>([]);
  const [selectedImageVariantKey, setSelectedImageVariantKey] = useState(DEFAULT_VARIANT_KEY);
  const [isCreateVariantModalOpen, setIsCreateVariantModalOpen] = useState(false);
  const [isImageStateLoading, setIsImageStateLoading] = useState(false);
  const [isImageVariantSaving, setIsImageVariantSaving] = useState(false);
  const [isImageGenerating, setIsImageGenerating] = useState(false);
  const [isImageVariantCreating, setIsImageVariantCreating] = useState(false);
  const [isImageVariantDeleting, setIsImageVariantDeleting] = useState(false);
  const [isImageInitializing, setIsImageInitializing] = useState(false);
  const [isBibleBatchUpdating, setIsBibleBatchUpdating] = useState(false);
  const [isEditImageModalOpen, setIsEditImageModalOpen] = useState(false);
  const [editImagePrompt, setEditImagePrompt] = useState('');
  const [isImageEditing, setIsImageEditing] = useState(false);

  // 圣经相关状态
  const [bibleImages, setBibleImages] = useState<Array<{ file_name: string; url: string; angle: string; expression: string; outfit: string }>>([]);
  const [bibleTask, setBibleTask] = useState<{ status: string; total: number; completed: number; failed: number } | null>(null);
  const [isBibleGenerating, setIsBibleGenerating] = useState(false);
  const [biblePollingTimer, setBiblePollingTimer] = useState<ReturnType<typeof setTimeout> | null>(null);
  const [isRegeneratingBible, setIsRegeneratingBible] = useState(false);
  const [editingBibleImage, setEditingBibleImage] = useState<{ file_name: string; url: string; angle: string; expression: string; outfit: string } | null>(null);
  const [isEditBibleImageModalOpen, setIsEditBibleImageModalOpen] = useState(false);
  const [editBibleImagePrompt, setEditBibleImagePrompt] = useState('');
  const [isBibleImageEditing, setIsBibleImageEditing] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const {
    refreshCharacters,
    deleteCharacter
  } = useCharacterSync();

  useEffect(() => {
    if (currentProject?.id) {
      void refreshCharacters();
      void fetchCareers();
      void loadCharacterImageStates();
    } else {
      setCharacterImageStates({});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProject?.id]);
  const [modal, contextHolder] = Modal.useModal();

  const mergeImageState = (state: CharacterImageState | CharacterImageActionResponse) => {
    setCharacterImageStates(prev => ({
      ...prev,
      [state.character_id]: state,
    }));
  };

  const mergeImageStates = (states: Array<CharacterImageState | CharacterImageActionResponse>) => {
    setCharacterImageStates(prev => {
      const next = { ...prev };
      states.forEach(state => {
        next[state.character_id] = state;
      });
      return next;
    });
  };

  const applySelectedVariantForm = (variant: CharacterImageState | null) => {
    if (!variant) {
      imageVariantForm.resetFields();
      return;
    }

    imageVariantForm.setFieldsValue({
      variant_label: variant.variant_label,
      variant_type: variant.variant_type,
      chapter_start: variant.chapter_start ?? null,
      chapter_end: variant.chapter_end ?? null,
      prompt: variant.prompt,
    });
  };

  const syncCurrentVariants = (variants: CharacterImageState[], preferredVariantKey?: string) => {
    setCurrentImageVariants(variants);

    const nextVariant =
      variants.find(variant => variant.variant_key === preferredVariantKey)
      ?? variants.find(variant => variant.variant_key === selectedImageVariantKey)
      ?? variants.find(variant => variant.variant_key === DEFAULT_VARIANT_KEY)
      ?? variants[0]
      ?? null;

    const nextVariantKey = nextVariant?.variant_key ?? DEFAULT_VARIANT_KEY;
    setSelectedImageVariantKey(nextVariantKey);
    applySelectedVariantForm(nextVariant);
  };

  const loadCharacterImageModalData = async (
    characterId: string,
    preferredVariantKey?: string,
    options?: { silent?: boolean }
  ) => {
    const silent = Boolean(options?.silent);
    const [state, variantResponse] = await Promise.all([
      characterImageApi.getCharacterState(characterId, { silent }),
      characterImageApi.getCharacterVariants(characterId, { silent }),
    ]);
    mergeImageState(state);
    syncCurrentVariants(variantResponse.items, preferredVariantKey);
    return { state, variants: variantResponse.items };
  };

  const fetchCareers = async () => {
    if (!currentProject?.id) return;
    try {
      const response = await api.get<unknown, { main_careers: Career[]; sub_careers: Career[] }>('/careers', {
        params: { project_id: currentProject.id }
      });
      setMainCareers(response.main_careers || []);
      setSubCareers(response.sub_careers || []);
    } catch (error) {
      console.error('获取职业列表失败:', error);
    }
  };

  const loadCharacterImageStates = async (options?: { silent?: boolean }) => {
    if (!currentProject?.id) return;
    try {
      const states = await characterImageApi.getProjectStates(currentProject.id, { silent: options?.silent });
      setCharacterImageStates(
        states.reduce<Record<string, CharacterImageState>>((acc, state) => {
          acc[state.character_id] = state;
          return acc;
        }, {})
      );
    } catch (error) {
      console.error('获取角色形象图状态失败:', error);
    }
  };

  const handleOpenCharacterImageModal = async (character: Character) => {
    setImageModalCharacter(character);
    setIsCharacterImageModalOpen(true);
    setIsImageStateLoading(true);
    try {
      await Promise.all([
        loadCharacterImageModalData(character.id, characterImageStates[character.id]?.variant_key),
        loadBibleImages(character.id),
      ]);
    } catch (error) {
      console.error('获取角色形象图详情失败:', error);
    } finally {
      setIsImageStateLoading(false);
    }
  };

  const handleCloseCharacterImageModal = () => {
    if (biblePollingTimer) {
      clearTimeout(biblePollingTimer);
      setBiblePollingTimer(null);
    }
    setIsCharacterImageModalOpen(false);
    setImageModalCharacter(null);
    setCurrentImageVariants([]);
    setSelectedImageVariantKey(DEFAULT_VARIANT_KEY);
    imageVariantForm.resetFields();
    createVariantForm.resetFields();
    setIsCreateVariantModalOpen(false);
    setIsImageStateLoading(false);
    setBibleImages([]);
    setBibleTask(null);
    setIsBibleGenerating(false);
    setIsImageVariantSaving(false);
    setIsImageGenerating(false);
    setIsImageVariantCreating(false);
    setIsImageVariantDeleting(false);
  };

  const handleSelectImageVariant = (variantKey: string) => {
    setSelectedImageVariantKey(variantKey);
    const variant = currentImageVariants.find(item => item.variant_key === variantKey) ?? null;
    applySelectedVariantForm(variant);
  };

  const handleSaveCharacterImageVariant = async () => {
    if (!imageModalCharacter) return;
    const currentVariant = currentImageVariants.find(variant => variant.variant_key === selectedImageVariantKey);
    if (!currentVariant) {
      return;
    }

    try {
      const values = await imageVariantForm.validateFields();
      setIsImageVariantSaving(true);

      const payload = currentVariant.variant_key === DEFAULT_VARIANT_KEY
        ? {
            prompt: values.prompt.trim(),
          }
        : {
            variant_label: values.variant_label.trim(),
            variant_type: values.variant_type,
            chapter_start: values.chapter_start ?? null,
            chapter_end: values.chapter_end ?? null,
            prompt: values.prompt.trim(),
          };

      const response = await characterImageApi.updateVariant(
        imageModalCharacter.id,
        currentVariant.variant_key,
        payload
      );
      await loadCharacterImageModalData(imageModalCharacter.id, response.variant_key);
      message.success(response.message);
    } catch (error) {
      console.error('保存角色形象版本失败:', error);
    } finally {
      setIsImageVariantSaving(false);
    }
  };

  const handleGenerateCharacterImage = async (overwrite: boolean = true) => {
    if (!imageModalCharacter) return;
    const currentVariant = currentImageVariants.find(variant => variant.variant_key === selectedImageVariantKey);
    if (!currentVariant) {
      return;
    }
    try {
      setIsImageGenerating(true);
      const response = await characterImageApi.generateVariantImage(
        imageModalCharacter.id,
        currentVariant.variant_key,
        overwrite
      );
      await loadCharacterImageModalData(imageModalCharacter.id, currentVariant.variant_key, { silent: true });
      await loadCharacterImageStates({ silent: true });

      if (response.queued || response.status === 'generating') {
        message.success(response.message || '角色形象图已进入后台生成队列');
        const characterId = imageModalCharacter.id;
        const variantKey = currentVariant.variant_key;
        window.setTimeout(async () => {
          for (let attempt = 0; attempt < 120; attempt += 1) {
            await new Promise(resolve => window.setTimeout(resolve, 5000));
            try {
              const latest = await characterImageApi.getCharacterVariants(characterId, { silent: true });
              const latestVariant = latest.items.find(variant => variant.variant_key === variantKey);
              if (!latestVariant || latestVariant.status === 'generating') {
                continue;
              }
              await loadCharacterImageModalData(characterId, variantKey, { silent: true });
              await loadCharacterImageStates({ silent: true });
              if (latestVariant.status === 'ready' || latestVariant.has_image) {
                message.success('角色形象图生成完成');
              } else if (latestVariant.status === 'capacity' || latestVariant.status === 'policy') {
                message.warning(latestVariant.error || '角色形象图暂未生成成功，请稍后重试');
              } else if (latestVariant.status === 'failed') {
                message.error(latestVariant.error || '角色形象图生成失败');
              }
              break;
            } catch (pollError) {
              console.error('轮询角色形象图状态失败:', pollError);
            }
          }
        }, 1000);
      } else if (response.status === 'ready') {
        message.success(response.message);
      } else if (response.status === 'capacity' || response.status === 'policy') {
        message.warning(response.message);
      } else {
        message.error(response.message);
      }
    } catch (error) {
      console.error('生成角色形象图失败:', error);
    } finally {
      setIsImageGenerating(false);
    }
  };

  const handleOpenEditImageModal = () => {
    if (!imageModalCharacter || !selectedImageVariant?.has_image) return;
    setEditImagePrompt('');
    setIsEditImageModalOpen(true);
  };

  const handleEditCharacterImage = async () => {
    if (!imageModalCharacter || !selectedImageVariant) return;
    if (!editImagePrompt.trim()) {
      message.warning('请输入改图提示词');
      return;
    }
    try {
      setIsImageEditing(true);
      setIsEditImageModalOpen(false);
      const response = await characterImageApi.editVariantImage(
        imageModalCharacter.id,
        selectedImageVariant.variant_key,
        editImagePrompt.trim()
      );
      await loadCharacterImageModalData(imageModalCharacter.id, selectedImageVariant.variant_key, { silent: true });
      await loadCharacterImageStates({ silent: true });

      if (response.queued || response.status === 'generating') {
        message.success(response.message || '角色形象图改图任务已进入后台队列');
        const characterId = imageModalCharacter.id;
        const variantKey = selectedImageVariant.variant_key;
        window.setTimeout(async () => {
          for (let attempt = 0; attempt < 120; attempt += 1) {
            await new Promise(resolve => window.setTimeout(resolve, 5000));
            try {
              const latest = await characterImageApi.getCharacterVariants(characterId, { silent: true });
              const latestVariant = latest.items.find(variant => variant.variant_key === variantKey);
              if (!latestVariant || latestVariant.status === 'generating') {
                continue;
              }
              await loadCharacterImageModalData(characterId, variantKey, { silent: true });
              await loadCharacterImageStates({ silent: true });
              if (latestVariant.status === 'ready' || latestVariant.has_image) {
                message.success('角色形象图改图完成');
              } else if (latestVariant.status === 'capacity' || latestVariant.status === 'policy') {
                message.warning(latestVariant.error || '角色形象图改图暂未成功，请稍后重试');
              } else if (latestVariant.status === 'failed') {
                message.error(latestVariant.error || '角色形象图改图失败');
              }
              break;
            } catch (pollError) {
              console.error('轮询角色形象图改图状态失败:', pollError);
            }
          }
        }, 1000);
      } else if (response.status === 'ready') {
        message.success(response.message);
      } else if (response.status === 'capacity' || response.status === 'policy') {
        message.warning(response.message);
      } else {
        message.error(response.message);
      }
    } catch (error) {
      console.error('角色形象图改图失败:', error);
    } finally {
      setIsImageEditing(false);
    }
  };

  const handleOpenCreateVariantModal = (variantType: CharacterImageVariantType) => {
    createVariantForm.resetFields();
    createVariantForm.setFieldsValue({
      variant_type: variantType,
      chapter_start: null,
      chapter_end: null,
      prompt: '',
    });
    setIsCreateVariantModalOpen(true);
  };

  const handleCreateImageVariant = async () => {
    if (!imageModalCharacter) return;

    try {
      const values = await createVariantForm.validateFields();
      setIsImageVariantCreating(true);
      const payload: CharacterImageVariantCreateRequest = {
        variant_label: values.variant_label.trim(),
        variant_type: values.variant_type,
        chapter_start: values.chapter_start ?? null,
        chapter_end: values.chapter_end ?? null,
        prompt: values.prompt?.trim() || undefined,
      };
      const response = await characterImageApi.createVariant(imageModalCharacter.id, payload);
      await loadCharacterImageModalData(imageModalCharacter.id, response.variant_key);
      setIsCreateVariantModalOpen(false);
      createVariantForm.resetFields();
      message.success(response.message);
    } catch (error) {
      console.error('创建角色形象版本失败:', error);
    } finally {
      setIsImageVariantCreating(false);
    }
  };

  const handleDeleteImageVariant = async () => {
    if (!imageModalCharacter || selectedImageVariantKey === DEFAULT_VARIANT_KEY) return;

    try {
      setIsImageVariantDeleting(true);
      await characterImageApi.deleteVariant(imageModalCharacter.id, selectedImageVariantKey);
      await loadCharacterImageModalData(imageModalCharacter.id, DEFAULT_VARIANT_KEY);
      message.success('形象版本已删除');
    } catch (error) {
      console.error('删除角色形象版本失败:', error);
    } finally {
      setIsImageVariantDeleting(false);
    }
  };

  const handleInitializeCharacterImages = async () => {
    if (!currentProject?.id) return;
    try {
      setIsImageInitializing(true);
      const response = await characterImageApi.initializeProject(currentProject.id, { overwrite: false });
      mergeImageStates(response.items);
      const characterCount = response.character_processed ?? response.character_candidates ?? 0;
      const organizationCount = response.organization_processed ?? response.organization_candidates ?? 0;
      message.success(`初始化完成：角色 ${characterCount} 个，组织 ${organizationCount} 个；生成 ${response.generated} 个，跳过 ${response.skipped} 个，失败 ${response.failed} 个`);
    } catch (error) {
      console.error('初始化角色/组织形象图失败:', error);
    } finally {
      setIsImageInitializing(false);
    }
  };

  // ── 批量更新视觉圣经 ──
  const handleBatchUpdateVisualBible = async () => {
    if (!currentProject?.id) return;
    try {
      setIsBibleBatchUpdating(true);
      const res = await characterApi.batchUpdateVisualBible(currentProject.id);
      if (res.status === 'no_action') {
        message.info('所有角色都已有视觉圣经数据');
        setIsBibleBatchUpdating(false);
        return;
      }
      message.info(`开始为 ${res.total} 个角色生成视觉圣经...`);
      // 轮询状态
      let attempts = 0;
      const poll = async () => {
        attempts++;
        try {
          const status = await characterApi.getBatchVisualBibleStatus(currentProject.id, { silent: true });
          if (status.status === 'generating' && attempts < 60) {
            setTimeout(poll, 5000);
          } else {
            setIsBibleBatchUpdating(false);
            if (status.status === 'completed') {
              message.success(`视觉圣经生成完成：成功 ${status.completed}，失败 ${status.failed}`);
              await refreshCharacters(undefined, { silent: true });
            } else if (status.status === 'failed') {
              message.error('视觉圣经批量生成失败');
            }
          }
        } catch (error) {
          console.error('轮询视觉圣经批量状态失败:', error);
          if (attempts < 60) {
            setTimeout(poll, 5000);
          } else {
            setIsBibleBatchUpdating(false);
          }
        }
      };
      setTimeout(poll, 5000);
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '启动失败';
      message.error(msg);
      setIsBibleBatchUpdating(false);
    }
  };

  // ── 角色圣经相关 ──
  const loadBibleImages = async (characterId: string, options?: { silent?: boolean }) => {
    try {
      const res = await characterImageApi.getBibleImages(characterId, { silent: options?.silent });
      setBibleImages(res.images || []);
      setBibleTask(res.task || null);
      return res;
    } catch {
      setBibleImages([]);
      setBibleTask(null);
      return null;
    }
  };

  const pollBibleTask = (characterId: string) => {
    let attempts = 0;
    const maxAttempts = 120;
    const poll = async () => {
      attempts++;
      const res = await loadBibleImages(characterId, { silent: true });
      if (!res) {
        if (attempts < maxAttempts) {
          const timer = setTimeout(poll, 5000);
          setBiblePollingTimer(timer);
        } else {
          setIsBibleGenerating(false);
        }
        return;
      }
      const task = res?.task;
      if (task && task.status === 'generating' && attempts < maxAttempts) {
        const timer = setTimeout(poll, 5000);
        setBiblePollingTimer(timer);
      } else {
        setIsBibleGenerating(false);
        if (task?.status === 'completed') {
          message.success(`圣经图片生成完成：${task.completed} 张，失败 ${task.failed} 张`);
        } else if (task?.status === 'failed') {
          message.error('圣经图片生成失败');
        }
      }
    };
    void poll();
  };

  const handleGenerateBibleImages = async () => {
    if (!imageModalCharacter) return;
    try {
      setIsBibleGenerating(true);
      const res = await characterImageApi.generateBibleImages(imageModalCharacter.id);
      message.info(`开始生成圣经图片，共 ${res.total} 张`);
      pollBibleTask(imageModalCharacter.id);
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '启动圣经图片生成失败';
      message.error(msg);
      setIsBibleGenerating(false);
    }
  };

  const handleDeleteBibleImage = async (fileName: string) => {
    if (!imageModalCharacter) return;
    try {
      await characterImageApi.deleteBibleImage(imageModalCharacter.id, fileName);
      setBibleImages(prev => prev.filter(img => img.file_name !== fileName));
      message.success('图片已删除');
    } catch {
      message.error('删除失败');
    }
  };

  const handleRegenerateVisualBible = async () => {
    if (!imageModalCharacter) return;
    try {
      setIsRegeneratingBible(true);
      const res = await characterApi.regenerateVisualBible(imageModalCharacter.id);
      if (res.visual_bible) {
        setImageModalCharacter(prev => prev ? { ...prev, visual_bible: res.visual_bible } : prev);
        await refreshCharacters();
        message.success('视觉圣经已生成');
      }
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '生成失败，请重试';
      message.error(msg);
    } finally {
      setIsRegeneratingBible(false);
    }
  };

  const handleSubmitEditBibleImage = async () => {
    if (!imageModalCharacter || !editingBibleImage) return;
    if (!editBibleImagePrompt.trim()) {
      message.warning('请输入改图提示词');
      return;
    }
    try {
      setIsBibleImageEditing(true);
      setIsEditBibleImageModalOpen(false);
      await characterImageApi.regenerateBibleImage(
        imageModalCharacter.id,
        editingBibleImage.file_name,
        editBibleImagePrompt.trim()
      );
      await loadBibleImages(imageModalCharacter.id);
      message.success('改图完成');
      setEditBibleImagePrompt('');
      setEditingBibleImage(null);
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : '改图失败';
      message.error(msg);
    } finally {
      setIsBibleImageEditing(false);
    }
  };

  if (!currentProject) return null;

  const handleDeleteCharacter = async (id: string) => {
    try {
      await deleteCharacter(id);
      setCharacterImageStates(prev => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      message.success('删除成功');
    } catch {
      message.error('删除失败');
    }
  };

  const handleGenerate = async (values: { name?: string; role_type: string; background?: string }) => {
    try {
      setIsGenerating(true);
      setProgress(0);
      setProgressMessage('准备生成角色...');

      const client = new SSEPostClient(
        buildApiPath('/characters/generate-stream'),
        {
          project_id: currentProject.id,
          name: values.name,
          role_type: values.role_type,
          background: values.background,
        },
        {
          onProgress: (msg, prog) => {
            setProgress(prog);
            setProgressMessage(msg);
          },
          onResult: (data) => {
            console.log('角色生成完成:', data);
          },
          onError: (error) => {
            message.error(`生成失败: ${error}`);
          },
          onComplete: () => {
            setProgress(100);
            setProgressMessage('生成完成！');
          }
        }
      );

      await client.connect();
      message.success('AI生成角色成功');
      Modal.destroyAll();
      await refreshCharacters();
      await loadCharacterImageStates();
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'AI生成失败';
      message.error(errorMessage);
    } finally {
      setTimeout(() => {
        setIsGenerating(false);
        setProgress(0);
        setProgressMessage('');
      }, 500);
    }
  };

  const handleGenerateOrganization = async (values: {
    name?: string;
    organization_type?: string;
    background?: string;
    requirements?: string;
  }) => {
    try {
      setIsGenerating(true);
      setProgress(0);
      setProgressMessage('准备生成组织...');

      const client = new SSEPostClient(
        buildApiPath('/organizations/generate-stream'),
        {
          project_id: currentProject.id,
          name: values.name,
          organization_type: values.organization_type,
          background: values.background,
          requirements: values.requirements,
        },
        {
          onProgress: (msg, prog) => {
            setProgress(prog);
            setProgressMessage(msg);
          },
          onResult: (data) => {
            console.log('组织生成完成:', data);
          },
          onError: (error) => {
            message.error(`生成失败: ${error}`);
          },
          onComplete: () => {
            setProgress(100);
            setProgressMessage('生成完成！');
          }
        }
      );

      await client.connect();
      message.success('AI生成组织成功');
      Modal.destroyAll();
      await refreshCharacters();
      await loadCharacterImageStates();
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : 'AI生成失败';
      message.error(errorMessage);
    } finally {
      setTimeout(() => {
        setIsGenerating(false);
        setProgress(0);
        setProgressMessage('');
      }, 500);
    }
  };

  const handleCreateCharacter = async (values: CharacterFormValues) => {
    try {
      const createData: CharacterCreateData = {
        project_id: currentProject.id,
        name: values.name,
        is_organization: createType === 'organization',
      };

      if (createType === 'character') {
        // 角色字段
        createData.age = values.age;
        createData.gender = values.gender;
        createData.role_type = values.role_type || 'supporting';
        createData.personality = values.personality;
        createData.appearance = values.appearance;
        createData.background = values.background;
        
        // 职业字段
        if (values.main_career_id) {
          createData.main_career_id = values.main_career_id;
          createData.main_career_stage = values.main_career_stage || 1;
        }
        
        // 处理副职业数据
        if (values.sub_career_data && Array.isArray(values.sub_career_data) && values.sub_career_data.length > 0) {
          createData.sub_careers = JSON.stringify(values.sub_career_data);
        }
      } else {
        // 组织字段
        createData.organization_type = values.organization_type;
        createData.organization_purpose = values.organization_purpose;
        createData.background = values.background;
        createData.power_level = values.power_level;
        createData.location = values.location;
        createData.motto = values.motto;
        createData.color = values.color;
        createData.role_type = 'supporting'; // 组织默认为配角
      }

      await characterApi.createCharacter(createData);
      message.success(`${createType === 'character' ? '角色' : '组织'}创建成功`);
      setIsCreateModalOpen(false);
      createForm.resetFields();
      await refreshCharacters();
      await loadCharacterImageStates();
    } catch {
      message.error('创建失败');
    }
  };

  const handleEditCharacter = (character: Character) => {
    setEditingCharacter(character);

    // 提取副职业数据（包含职业ID和阶段）
    const subCareerData: SubCareerData[] = character.sub_careers?.map((sc) => ({
      career_id: sc.career_id,
      stage: sc.stage || 1
    })) || [];

    editForm.setFieldsValue({
      ...character,
      sub_career_data: subCareerData
    });
    setIsEditModalOpen(true);
  };

  const handleUpdateCharacter = async (values: CharacterFormValues) => {
    if (!editingCharacter) return;

    try {
      // 提取副职业数据，剩余的作为更新数据
      const { sub_career_data: subCareerData, ...restValues } = values;
      const updateData: CharacterUpdateData = { ...restValues };

      // 转换为sub_careers格式
      if (subCareerData && Array.isArray(subCareerData) && subCareerData.length > 0) {
        updateData.sub_careers = JSON.stringify(subCareerData);
      } else {
        updateData.sub_careers = JSON.stringify([]);
      }

      await characterApi.updateCharacter(editingCharacter.id, updateData);
      message.success('更新成功');
      setIsEditModalOpen(false);
      editForm.resetFields();
      setEditingCharacter(null);
      await refreshCharacters();
      await loadCharacterImageStates();
    } catch (error) {
      console.error('更新失败:', error);
      message.error('更新失败');
    }
  };

  const handleDeleteCharacterWrapper = (id: string) => {
    handleDeleteCharacter(id);
  };

  // 导出选中的角色/组织
  const handleExportSelected = async () => {
    if (selectedCharacters.length === 0) {
      message.warning('请至少选择一个角色或组织');
      return;
    }

    try {
      await characterApi.exportCharacters(selectedCharacters);
      message.success(`成功导出 ${selectedCharacters.length} 个角色/组织`);
      setSelectedCharacters([]);
    } catch (error) {
      message.error('导出失败');
      console.error('导出错误:', error);
    }
  };

  // 导出单个角色/组织
  const handleExportSingle = async (characterId: string) => {
    try {
      await characterApi.exportCharacters([characterId]);
      message.success('导出成功');
    } catch (error) {
      message.error('导出失败');
      console.error('导出错误:', error);
    }
  };

  // 处理文件选择
  const handleFileSelect = async (file: File) => {
    try {
      // 验证文件
      const validation = await characterApi.validateImportCharacters(file);
      
      if (!validation.valid) {
        modal.error({
          title: '文件验证失败',
          centered: true,
          content: (
            <div>
              {validation.errors.map((error, index) => (
                <div key={index} style={{ color: token.colorError }}>• {error}</div>
              ))}
            </div>
          ),
        });
        return;
      }

      // 显示预览对话框
      modal.confirm({
        title: '导入预览',
        width: 500,
        centered: true,
        content: (
          <div>
            <p><strong>文件版本:</strong> {validation.version}</p>
            <Divider style={{ margin: '12px 0' }} />
            <p><strong>将要导入:</strong></p>
            <ul style={{ marginLeft: 20 }}>
              <li>角色: {validation.statistics.characters} 个</li>
              <li>组织: {validation.statistics.organizations} 个</li>
            </ul>
            {validation.warnings.length > 0 && (
              <>
                <Divider style={{ margin: '12px 0' }} />
                <p style={{ color: token.colorWarning }}><strong>⚠️ 警告:</strong></p>
                <ul style={{ marginLeft: 20 }}>
                  {validation.warnings.map((warning, index) => (
                    <li key={index} style={{ color: token.colorWarning }}>{warning}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        ),
        okText: '确认导入',
        cancelText: '取消',
        onOk: async () => {
          try {
            const result = await characterApi.importCharacters(currentProject.id, file);
            
            if (result.success) {
              // 显示导入结果
              modal.success({
                title: '导入完成',
                width: 600,
                centered: true,
                content: (
                  <div>
                    <p><strong>✅ 成功导入: {result.statistics.imported} 个</strong></p>
                    {result.details.imported_characters.length > 0 && (
                      <>
                        <p style={{ marginTop: 12, marginBottom: 4 }}>角色:</p>
                        <ul style={{ marginLeft: 20 }}>
                          {result.details.imported_characters.map((name, index) => (
                            <li key={index}>{name}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {result.details.imported_organizations.length > 0 && (
                      <>
                        <p style={{ marginTop: 12, marginBottom: 4 }}>组织:</p>
                        <ul style={{ marginLeft: 20 }}>
                          {result.details.imported_organizations.map((name, index) => (
                            <li key={index}>{name}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {result.statistics.skipped > 0 && (
                      <>
                        <Divider style={{ margin: '12px 0' }} />
                        <p style={{ color: token.colorWarning }}>⚠️ 跳过: {result.statistics.skipped} 个</p>
                        <ul style={{ marginLeft: 20 }}>
                          {result.details.skipped.map((name, index) => (
                            <li key={index} style={{ color: token.colorWarning }}>{name}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {result.warnings.length > 0 && (
                      <>
                        <Divider style={{ margin: '12px 0' }} />
                        <p style={{ color: token.colorWarning }}>⚠️ 警告:</p>
                        <ul style={{ marginLeft: 20 }}>
                          {result.warnings.map((warning, index) => (
                            <li key={index} style={{ color: token.colorWarning }}>{warning}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {result.details.errors.length > 0 && (
                      <>
                        <Divider style={{ margin: '12px 0' }} />
                        <p style={{ color: token.colorError }}>❌ 失败: {result.statistics.errors} 个</p>
                        <ul style={{ marginLeft: 20 }}>
                          {result.details.errors.map((error, index) => (
                            <li key={index} style={{ color: token.colorError }}>{error}</li>
                          ))}
                        </ul>
                      </>
                    )}
                  </div>
                ),
              });
              
              // 刷新列表
              await refreshCharacters();
              await loadCharacterImageStates();
              setIsImportModalOpen(false);
            } else {
              message.error(result.message || '导入失败');
            }
          } catch (error: unknown) {
            const apiError = error as ApiError;
            message.error(apiError.response?.data?.detail || '导入失败');
            console.error('导入错误:', error);
          }
        },
      });
    } catch (error: unknown) {
      const apiError = error as ApiError;
      message.error(apiError.response?.data?.detail || '文件验证失败');
      console.error('验证错误:', error);
    }
  };

  // 切换选择
  const toggleSelectCharacter = (id: string) => {
    setSelectedCharacters(prev =>
      prev.includes(id) ? prev.filter(cid => cid !== id) : [...prev, id]
    );
  };

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedCharacters.length === displayList.length) {
      setSelectedCharacters([]);
    } else {
      setSelectedCharacters(displayList.map(c => c.id));
    }
  };

  const showGenerateModal = () => {
    modal.confirm({
      title: 'AI生成角色',
      width: 600,
      centered: true,
      content: (
        <Form form={generateForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            label="角色名称"
            name="name"
          >
            <Input placeholder="如：张三、李四（可选，AI会自动生成）" />
          </Form.Item>
          <Form.Item
            label="角色定位"
            name="role_type"
            rules={[{ required: true, message: '请选择角色定位' }]}
          >
            <Select placeholder="选择角色定位">
              <Select.Option value="protagonist">主角</Select.Option>
              <Select.Option value="supporting">配角</Select.Option>
              <Select.Option value="antagonist">反派</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item label="背景设定" name="background">
            <TextArea rows={3} placeholder="简要描述角色背景和故事环境..." />
          </Form.Item>
        </Form>
      ),
      okText: '生成',
      cancelText: '取消',
      onOk: async () => {
        const values = await generateForm.validateFields();
        await handleGenerate(values);
      },
    });
  };

  const showGenerateOrgModal = () => {
    modal.confirm({
      title: 'AI生成组织',
      width: 600,
      centered: true,
      content: (
        <Form form={generateOrgForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item
            label="组织名称"
            name="name"
          >
            <Input placeholder="如：天剑门、黑龙会（可选，AI会自动生成）" />
          </Form.Item>
          <Form.Item
            label="组织类型"
            name="organization_type"
          >
            <Input placeholder="如：门派、帮派、公司、学院（可选，AI会根据世界观生成）" />
          </Form.Item>
          <Form.Item label="背景设定" name="background">
            <TextArea rows={3} placeholder="简要描述组织的背景和环境..." />
          </Form.Item>
          <Form.Item label="其他要求" name="requirements">
            <TextArea rows={2} placeholder="其他特殊要求..." />
          </Form.Item>
        </Form>
      ),
      okText: '生成',
      cancelText: '取消',
      onOk: async () => {
        const values = await generateOrgForm.validateFields();
        await handleGenerateOrganization(values);
      },
    });
  };

  const characterList = characters.filter(c => !c.is_organization);
  const organizationList = characters.filter(c => c.is_organization);

  const getDisplayList = () => {
    if (activeTab === 'character') return characterList;
    if (activeTab === 'organization') return organizationList;
    return characters;
  };

  const displayList = getDisplayList();
  const selectedImageVariant = useMemo(
    () => currentImageVariants.find(variant => variant.variant_key === selectedImageVariantKey) ?? currentImageVariants[0],
    [currentImageVariants, selectedImageVariantKey]
  );

  const isMobile = window.innerWidth <= 768;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {contextHolder}
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 10,
        backgroundColor: 'var(--color-bg-container)',
        padding: isMobile ? '12px 0' : '16px 0',
        marginBottom: isMobile ? 12 : 16,
        borderBottom: '1px solid var(--color-border-secondary)',
        display: 'flex',
        flexDirection: isMobile ? 'column' : 'row',
        gap: isMobile ? 12 : 0,
        justifyContent: 'space-between',
        alignItems: isMobile ? 'stretch' : 'center'
      }}>
        <h2 style={{ margin: 0, fontSize: isMobile ? 18 : 24 }}>
          <TeamOutlined style={{ marginRight: 8 }} />
          角色与组织管理
        </h2>
        <Space wrap>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setCreateType('character');
              setIsCreateModalOpen(true);
            }}
            size={isMobile ? 'small' : 'middle'}
          >
            创建角色
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setCreateType('organization');
              setIsCreateModalOpen(true);
            }}
            size={isMobile ? 'small' : 'middle'}
          >
            创建组织
          </Button>
          <Button
            type="dashed"
            icon={<ThunderboltOutlined />}
            onClick={showGenerateModal}
            loading={isGenerating}
            size={isMobile ? 'small' : 'middle'}
          >
            AI生成角色
          </Button>
          <Button
            type="dashed"
            icon={<ThunderboltOutlined />}
            onClick={showGenerateOrgModal}
            loading={isGenerating}
            size={isMobile ? 'small' : 'middle'}
          >
            AI生成组织
          </Button>
          <Button
            icon={<PictureOutlined />}
            onClick={handleInitializeCharacterImages}
            loading={isImageInitializing}
            size={isMobile ? 'small' : 'middle'}
          >
            初始化形象图
          </Button>
          <Button
            icon={<BookOutlined />}
            onClick={handleBatchUpdateVisualBible}
            loading={isBibleBatchUpdating}
            size={isMobile ? 'small' : 'middle'}
          >
            批量更新视觉圣经
          </Button>
          <Button
            icon={<ImportOutlined />}
            onClick={() => setIsImportModalOpen(true)}
            size={isMobile ? 'small' : 'middle'}
          >
            导入
          </Button>
          {selectedCharacters.length > 0 && (
            <Button
              icon={<ExportOutlined />}
              onClick={handleExportSelected}
              size={isMobile ? 'small' : 'middle'}
            >
              批量导出 ({selectedCharacters.length})
            </Button>
          )}
        </Space>
      </div>

      {characters.length > 0 && (
        <div style={{
          position: 'sticky',
          top: isMobile ? 60 : 72,
          zIndex: 9,
          backgroundColor: 'var(--color-bg-container)',
          paddingBottom: 8,
          borderBottom: '1px solid var(--color-border-secondary)',
        }}>
          <Tabs
            activeKey={activeTab}
            onChange={(key) => setActiveTab(key as 'all' | 'character' | 'organization')}
            items={[
              {
                key: 'all',
                label: `全部 (${characters.length})`,
              },
              {
                key: 'character',
                label: (
                  <span>
                    <UserOutlined /> 角色 ({characterList.length})
                  </span>
                ),
              },
              {
                key: 'organization',
                label: (
                  <span>
                    <TeamOutlined /> 组织 ({organizationList.length})
                  </span>
                ),
              },
            ]}
          />
        </div>
      )}

      {/* 批量选择工具栏 */}
      {characters.length > 0 && (
        <div style={{
          position: 'sticky',
          top: isMobile ? 120 : 132,
          zIndex: 8,
          backgroundColor: 'var(--color-bg-container)',
          paddingBottom: 8,
          paddingTop: 8,
          marginTop: 8,
          borderBottom: selectedCharacters.length > 0 ? '1px solid var(--color-border-secondary)' : 'none',
        }}>
          <Space>
            <Checkbox
              checked={selectedCharacters.length === displayList.length && displayList.length > 0}
              indeterminate={selectedCharacters.length > 0 && selectedCharacters.length < displayList.length}
              onChange={toggleSelectAll}
            >
              {selectedCharacters.length > 0 ? `已选 ${selectedCharacters.length} 个` : '全选'}
            </Checkbox>
            {selectedCharacters.length > 0 && (
              <Button
                type="link"
                size="small"
                onClick={() => setSelectedCharacters([])}
              >
                取消选择
              </Button>
            )}
          </Space>
        </div>
      )}

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {characters.length === 0 ? (
          <Empty description="还没有角色或组织，开始创建吧！" />
        ) : (
          <>
            <Row gutter={isMobile ? [8, 8] : charactersPageGridConfig.gutter}>
              {activeTab === 'all' && (
                <>
                  {characterList.length > 0 && (
                    <>
                      <Col span={24}>
                        <Divider orientation="left">
                          <Title level={5} style={{ margin: 0 }}>
                            <UserOutlined style={{ marginRight: 8 }} />
                            角色 ({characterList.length})
                          </Title>
                        </Divider>
                      </Col>
                      {characterList.map((character) => (
                        <Col
                          xs={24}
                          sm={charactersPageGridConfig.sm}
                          md={charactersPageGridConfig.md}
                          lg={charactersPageGridConfig.lg}
                          xl={charactersPageGridConfig.xl}
                          key={character.id}
                          style={{ padding: isMobile ? '4px' : '8px' }}
                        >
                          <div style={{ position: 'relative' }}>
                            <Checkbox
                              checked={selectedCharacters.includes(character.id)}
                              onChange={() => toggleSelectCharacter(character.id)}
                              style={{ position: 'absolute', top: 8, left: 8, zIndex: 1 }}
                            />
                            <CharacterCard
                              character={character}
                              imageState={characterImageStates[character.id]}
                              onEdit={handleEditCharacter}
                              onDelete={handleDeleteCharacterWrapper}
                              onExport={() => handleExportSingle(character.id)}
                              onManageImage={handleOpenCharacterImageModal}
                            />
                          </div>
                        </Col>
                      ))}
                    </>
                  )}

                  {organizationList.length > 0 && (
                    <>
                      <Col span={24}>
                        <Divider orientation="left">
                          <Title level={5} style={{ margin: 0 }}>
                            <TeamOutlined style={{ marginRight: 8 }} />
                            组织 ({organizationList.length})
                          </Title>
                        </Divider>
                      </Col>
                      {organizationList.map((org) => (
                        <Col
                          xs={24}
                          sm={charactersPageGridConfig.sm}
                          md={charactersPageGridConfig.md}
                          lg={charactersPageGridConfig.lg}
                          xl={charactersPageGridConfig.xl}
                          key={org.id}
                          style={{ padding: isMobile ? '4px' : '8px' }}
                        >
                          <div style={{ position: 'relative' }}>
                            <Checkbox
                              checked={selectedCharacters.includes(org.id)}
                              onChange={() => toggleSelectCharacter(org.id)}
                              style={{ position: 'absolute', top: 8, left: 8, zIndex: 1 }}
                            />
                            <CharacterCard
                              character={org}
                              imageState={characterImageStates[org.id]}
                              onEdit={handleEditCharacter}
                              onDelete={handleDeleteCharacterWrapper}
                              onExport={() => handleExportSingle(org.id)}
                              onManageImage={handleOpenCharacterImageModal}
                            />
                          </div>
                        </Col>
                      ))}
                    </>
                  )}
                </>
              )}

              {activeTab === 'character' && characterList.map((character) => (
                <Col
                  xs={24}
                  sm={charactersPageGridConfig.sm}
                  md={charactersPageGridConfig.md}
                  lg={charactersPageGridConfig.lg}
                  xl={charactersPageGridConfig.xl}
                  key={character.id}
                  style={{ padding: isMobile ? '4px' : '8px' }}
                >
                  <div style={{ position: 'relative' }}>
                    <Checkbox
                      checked={selectedCharacters.includes(character.id)}
                      onChange={() => toggleSelectCharacter(character.id)}
                      style={{ position: 'absolute', top: 8, left: 8, zIndex: 1 }}
                    />
                    <CharacterCard
                      character={character}
                      imageState={characterImageStates[character.id]}
                      onEdit={handleEditCharacter}
                      onDelete={handleDeleteCharacterWrapper}
                      onExport={() => handleExportSingle(character.id)}
                      onManageImage={handleOpenCharacterImageModal}
                    />
                  </div>
                </Col>
              ))}

              {activeTab === 'organization' && organizationList.map((org) => (
                <Col
                  xs={24}
                  sm={charactersPageGridConfig.sm}
                  md={charactersPageGridConfig.md}
                  lg={charactersPageGridConfig.lg}
                  xl={charactersPageGridConfig.xl}
                  key={org.id}
                  style={{ padding: isMobile ? '4px' : '8px' }}
                >
                  <div style={{ position: 'relative' }}>
                    <Checkbox
                      checked={selectedCharacters.includes(org.id)}
                      onChange={() => toggleSelectCharacter(org.id)}
                      style={{ position: 'absolute', top: 8, left: 8, zIndex: 1 }}
                    />
                    <CharacterCard
                      character={org}
                      imageState={characterImageStates[org.id]}
                      onEdit={handleEditCharacter}
                      onDelete={handleDeleteCharacterWrapper}
                      onExport={() => handleExportSingle(org.id)}
                      onManageImage={handleOpenCharacterImageModal}
                    />
                  </div>
                </Col>
              ))}
            </Row>

            {displayList.length === 0 && (
              <Empty
                description={
                  activeTab === 'character'
                    ? '暂无角色'
                    : activeTab === 'organization'
                      ? '暂无组织'
                      : '暂无数据'
                }
              />
            )}
          </>
        )}
      </div>

      <Modal
        title={imageModalCharacter ? `${imageModalCharacter.name} · AI形象版本` : 'AI形象版本'}
        open={isCharacterImageModalOpen}
        onCancel={handleCloseCharacterImageModal}
        width={isMobile ? '100%' : 980}
        style={isMobile ? { top: 0, paddingBottom: 0, maxWidth: '100vw' } : undefined}
        footer={
          <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
            <Button onClick={handleCloseCharacterImageModal}>关闭</Button>
            <Space wrap>
              <Button
                icon={<DownloadOutlined />}
                disabled={!selectedImageVariant?.image_url}
                onClick={() => {
                  if (selectedImageVariant?.image_url) {
                    window.open(buildAppPath(selectedImageVariant.image_url), '_blank', 'noopener,noreferrer');
                  }
                }}
              >
                查看当前版本原图
              </Button>
              <Button
                onClick={handleSaveCharacterImageVariant}
                loading={isImageVariantSaving}
                disabled={!imageModalCharacter || !selectedImageVariant}
              >
                保存当前版本
              </Button>
              <Button
                type="primary"
                icon={selectedImageVariant?.has_image ? <ReloadOutlined /> : <PictureOutlined />}
                onClick={() => handleGenerateCharacterImage(true)}
                loading={isImageGenerating}
                disabled={!imageModalCharacter || !selectedImageVariant}
              >
                {selectedImageVariant?.has_image ? '重新生成当前版本' : '生成当前版本'}
              </Button>
              {selectedImageVariant?.has_image && (
                <Button
                  onClick={handleOpenEditImageModal}
                  disabled={!imageModalCharacter || !selectedImageVariant}
                  loading={isImageEditing}
                >
                  改图
                </Button>
              )}
            </Space>
          </Space>
        }
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 120px)' : 'calc(100vh - 200px)',
            overflowY: 'auto',
          }
        }}
      >
        {imageModalCharacter && (
          <Spin spinning={isImageStateLoading}>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="可按分卷或时期维护不同角色形象。漫画生成会按章节范围优先使用匹配版本，未命中时回退默认形象。"
            />
            <Row gutter={[16, 16]} style={{ marginTop: 8 }}>
              <Col xs={24} md={8}>
                <Card size="small" title="形象版本" extra={
                  <Space>
                    <Button size="small" icon={<PlusOutlined />} onClick={() => handleOpenCreateVariantModal('volume')}>分卷</Button>
                    <Button size="small" icon={<PlusOutlined />} onClick={() => handleOpenCreateVariantModal('period')}>时期</Button>
                  </Space>
                }>
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {currentImageVariants.map(variant => (
                      <Card
                        key={variant.variant_key}
                        size="small"
                        hoverable
                        onClick={() => handleSelectImageVariant(variant.variant_key)}
                        style={{ borderColor: selectedImageVariantKey === variant.variant_key ? token.colorPrimary : token.colorBorderSecondary }}
                      >
                        <Space direction="vertical" size={4} style={{ width: '100%' }}>
                          <Space wrap>
                            <Tag color={variant.variant_key === DEFAULT_VARIANT_KEY ? 'blue' : 'purple'}>
                              {variant.variant_label}
                            </Tag>
                            <Tag>{IMAGE_VARIANT_TYPE_TEXT[variant.variant_type] || variant.variant_type}</Tag>
                            <Tag color={IMAGE_STATUS_COLOR[variant.status || 'none']}>
                              {IMAGE_STATUS_TEXT[variant.status || 'none'] || variant.status}
                            </Tag>
                          </Space>
                          <Space size={4}>
                            <Text type="secondary" style={{ fontSize: 12 }}>
                              {formatVariantRange(variant) || '全书默认'}
                            </Text>
                            {variant.has_image && (
                              <Button
                                size="small"
                                type="link"
                                style={{ fontSize: 12, padding: 0 }}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleSelectImageVariant(variant.variant_key);
                                  setEditImagePrompt('');
                                  setIsEditImageModalOpen(true);
                                }}
                              >
                                改图
                              </Button>
                            )}
                          </Space>
                        </Space>
                      </Card>
                    ))}
                  </Space>
                </Card>
              </Col>
              <Col xs={24} md={7}>
                <Card
                  size="small"
                  style={{
                    borderRadius: 16,
                    overflow: 'hidden',
                    background: 'linear-gradient(180deg, rgba(59, 130, 246, 0.08), rgba(217, 119, 6, 0.08))',
                  }}
                >
                  {selectedImageVariant?.image_url ? (
                    <Image
                      key={selectedImageVariant.image_url}
                      src={buildAppPath(selectedImageVariant.image_url)}
                      alt={`${imageModalCharacter.name}形象图`}
                      style={{ width: '100%', aspectRatio: '1 / 1', objectFit: 'cover', borderRadius: 12 }}
                    />
                  ) : (
                    <div style={{ aspectRatio: '1 / 1', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, color: token.colorTextTertiary }}>
                      <PictureOutlined style={{ fontSize: 36 }} />
                      <Text type="secondary">当前版本还没有形象图</Text>
                    </div>
                  )}
                </Card>
                <Space wrap style={{ marginTop: 12 }}>
                  <Tag color={IMAGE_STATUS_COLOR[selectedImageVariant?.status || 'none']}>
                    {IMAGE_STATUS_TEXT[selectedImageVariant?.status || 'none'] || selectedImageVariant?.status || '未知状态'}
                  </Tag>
                  {selectedImageVariant?.updated_at && <Text type="secondary" style={{ fontSize: 12 }}>更新时间：{selectedImageVariant.updated_at}</Text>}
                </Space>
                {selectedImageVariant?.error && (
                  <Alert style={{ marginTop: 12 }} type={selectedImageVariant.status === 'policy' ? 'warning' : 'error'} showIcon message={selectedImageVariant.error} />
                )}
              </Col>
              <Col xs={24} md={9}>
                <Form form={imageVariantForm} layout="vertical">
                  <Row gutter={8}>
                    <Col span={12}>
                      <Form.Item label="版本名称" name="variant_label" rules={[{ required: true, message: '请输入版本名称' }]}>
                        <Input disabled={selectedImageVariantKey === DEFAULT_VARIANT_KEY} placeholder="如：第一卷初入都市" />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="版本类型" name="variant_type" rules={[{ required: true, message: '请选择版本类型' }]}>
                        <Select disabled={selectedImageVariantKey === DEFAULT_VARIANT_KEY}>
                          <Select.Option value="default">默认形象</Select.Option>
                          <Select.Option value="volume">分卷</Select.Option>
                          <Select.Option value="period">时期/阶段</Select.Option>
                        </Select>
                      </Form.Item>
                    </Col>
                  </Row>
                  <Row gutter={8}>
                    <Col span={12}>
                      <Form.Item label="起始章节" name="chapter_start">
                        <InputNumber disabled={selectedImageVariantKey === DEFAULT_VARIANT_KEY} min={1} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item label="结束章节" name="chapter_end">
                        <InputNumber disabled={selectedImageVariantKey === DEFAULT_VARIANT_KEY} min={1} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Form.Item label="形象提示词" name="prompt" rules={[{ required: true, message: '请输入形象提示词' }]} extra="每个版本可保存独立提示词；生成或重生成只作用于当前选中的版本。">
                    <TextArea rows={10} placeholder="描述该时期/分卷的外观、气质、服装、状态变化..." maxLength={2000} showCount />
                  </Form.Item>
                </Form>
                <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Popconfirm title="确定删除这个形象版本吗？" disabled={selectedImageVariantKey === DEFAULT_VARIANT_KEY} onConfirm={handleDeleteImageVariant} okText="删除" cancelText="取消">
                    <Button danger disabled={selectedImageVariantKey === DEFAULT_VARIANT_KEY} loading={isImageVariantDeleting}>删除版本</Button>
                  </Popconfirm>
                  <Text type="secondary">默认形象不可删除，作为漫画兜底形象。</Text>
                </Space>
              </Col>
            </Row>

            {/* ── 角色圣经 ── */}
            {imageModalCharacter && !imageModalCharacter.is_organization && (
              <div style={{ marginTop: 24 }}>
                <Divider orientation="left" plain>
                  <Text strong>角色视觉圣经</Text>
                  {!imageModalCharacter.visual_bible && (
                    <Button
                      type="link"
                      size="small"
                      icon={<ThunderboltOutlined />}
                      onClick={handleRegenerateVisualBible}
                      loading={isRegeneratingBible}
                      style={{ marginLeft: 12 }}
                    >
                      生成角色圣经
                    </Button>
                  )}
                </Divider>

                {imageModalCharacter.visual_bible ? (
                  <>
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={handleRegenerateVisualBible}
                        loading={isRegeneratingBible}
                      >
                        重新生成
                      </Button>
                    </div>

                    <Row gutter={[16, 16]}>
                      <Col xs={24} md={12}>
                        <Card size="small" title="不可变特征" type="inner">
                          {imageModalCharacter.visual_bible.immutable_traits &&
                            Object.entries(imageModalCharacter.visual_bible.immutable_traits).map(([key, val]) => (
                              <div key={key} style={{ marginBottom: 4 }}>
                                <Text type="secondary" style={{ fontSize: 12 }}>{key}：</Text>
                                <Text style={{ fontSize: 13 }}>{String(val)}</Text>
                              </div>
                            ))
                          }
                        </Card>
                      </Col>
                      <Col xs={24} md={12}>
                        <Card size="small" title="禁止特征" type="inner">
                          <Space wrap>
                            {imageModalCharacter.visual_bible.forbidden_traits?.map((t, i) => (
                              <Tag key={i} color="red">{t}</Tag>
                            ))}
                          </Space>
                        </Card>
                      </Col>
                      <Col xs={24} md={12}>
                        <Card size="small" title="视角描述" type="inner">
                          {imageModalCharacter.visual_bible.views?.map((v, i) => (
                            <div key={i} style={{ marginBottom: 4 }}>
                              <Tag>{v.angle}</Tag>
                              <Text style={{ fontSize: 13 }}>{v.description}</Text>
                            </div>
                          ))}
                        </Card>
                      </Col>
                      <Col xs={24} md={12}>
                        <Card size="small" title="表情" type="inner">
                          {imageModalCharacter.visual_bible.expressions?.map((e, i) => (
                            <div key={i} style={{ marginBottom: 4 }}>
                              <Tag color="cyan">{e.name}</Tag>
                              <Text style={{ fontSize: 13 }}>{e.description}</Text>
                            </div>
                          ))}
                        </Card>
                      </Col>
                      <Col xs={24} md={12}>
                        <Card size="small" title="服装" type="inner">
                          {imageModalCharacter.visual_bible.outfits?.map((o, i) => (
                            <div key={i} style={{ marginBottom: 4 }}>
                              <Tag color="purple">{o.name}</Tag>
                              <Text style={{ fontSize: 13 }}>{o.description}</Text>
                            </div>
                          ))}
                        </Card>
                      </Col>
                      <Col xs={24} md={12}>
                        <Card size="small" title="训练描述" type="inner">
                          <Text style={{ fontSize: 13 }}>{imageModalCharacter.visual_bible.training_caption}</Text>
                          <div style={{ marginTop: 8 }}>
                            <Text type="secondary" style={{ fontSize: 12 }}>触发词：</Text>
                            <Tag color="green">{imageModalCharacter.visual_bible.trigger_token}</Tag>
                          </div>
                        </Card>
                      </Col>
                    </Row>

                    {/* 多视角图片 */}
                    <div style={{ marginTop: 16 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                        <Text strong>多视角图片</Text>
                        <Space>
                          <Button
                            type="primary"
                            icon={<PictureOutlined />}
                            onClick={handleGenerateBibleImages}
                            loading={isBibleGenerating}
                          >
                            {isBibleGenerating ? '生成中...' : '批量生成'}
                          </Button>
                        </Space>
                      </div>

                      {bibleTask && bibleTask.status === 'generating' && (
                        <Alert
                          type="info"
                          showIcon
                          style={{ marginBottom: 12 }}
                          message={`正在生成中：${bibleTask.completed}/${bibleTask.total}，失败 ${bibleTask.failed}`}
                        />
                      )}

                      {bibleImages.length > 0 ? (
                        <Row gutter={[8, 8]}>
                          {bibleImages.map(img => (
                            <Col key={img.file_name} xs={12} sm={8} md={6} lg={4}>
                              <Card
                                size="small"
                                hoverable
                                cover={
                                  <Image
                                    src={buildAppPath(img.url)}
                                    alt={img.file_name}
                                    style={{ aspectRatio: '1 / 1', objectFit: 'cover' }}
                                    preview={{ mask: <span>预览</span> }}
                                  />
                                }
                                actions={[
                                  <Button
                                    key="edit"
                                    type="link"
                                    size="small"
                                    onClick={() => {
                                      setEditingBibleImage(img);
                                      setEditBibleImagePrompt('');
                                      setIsEditBibleImageModalOpen(true);
                                    }}
                                  >
                                    改图
                                  </Button>,
                                  <Button key="del" type="link" danger size="small" onClick={() => handleDeleteBibleImage(img.file_name)}>
                                    删除
                                  </Button>,
                                ]}
                              >
                                <Card.Meta
                                  title={<Text style={{ fontSize: 12 }}>{img.angle}</Text>}
                                  description={
                                    <Space direction="vertical" size={0}>
                                      {img.expression && <Text type="secondary" style={{ fontSize: 11 }}>{img.expression}</Text>}
                                      {img.outfit && <Text type="secondary" style={{ fontSize: 11 }}>{img.outfit}</Text>}
                                    </Space>
                                  }
                                />
                              </Card>
                            </Col>
                          ))}
                        </Row>
                      ) : (
                        !isBibleGenerating && (
                          <Empty
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                            description="暂无圣经图片，点击批量生成"
                            style={{ padding: '24px 0' }}
                          />
                        )
                      )}
                    </div>
                  </>
                ) : (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无角色圣经，点击上方按钮生成"
                    style={{ padding: '24px 0' }}
                  />
                )}
              </div>
            )}
          </Spin>
        )}
      </Modal>

      <Modal
        title="改图 - 基于原图修改"
        open={isEditImageModalOpen}
        onCancel={() => setIsEditImageModalOpen(false)}
        onOk={handleEditCharacterImage}
        confirmLoading={isImageEditing}
        okText="开始改图"
        cancelText="取消"
        width={isMobile ? '100%' : 600}
      >
        {selectedImageVariant?.image_url && (
          <div style={{ textAlign: 'center', marginBottom: 16 }}>
            <Image
              src={buildAppPath(selectedImageVariant.image_url)}
              alt="当前形象图"
              style={{ maxWidth: '100%', maxHeight: 300, borderRadius: 8 }}
            />
          </div>
        )}
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary">输入改图提示词，AI 会基于当前形象图进行修改。描述你希望改变的部分，如：更换服装颜色、改变发型、添加配饰等。</Text>
        </div>
        <TextArea
          rows={6}
          value={editImagePrompt}
          onChange={e => setEditImagePrompt(e.target.value)}
          placeholder="描述你想要修改的内容，例如：将服装改为红色长裙，背景改为夕阳下的海滩..."
          maxLength={2000}
          showCount
        />
      </Modal>

      {/* ── 圣经图片改图 Modal ── */}
      <Modal
        title={editingBibleImage ? `改图 - ${editingBibleImage.angle} ${editingBibleImage.expression || ''} ${editingBibleImage.outfit || ''}` : '改图'}
        open={isEditBibleImageModalOpen}
        onCancel={() => { setIsEditBibleImageModalOpen(false); setEditingBibleImage(null); setEditBibleImagePrompt(''); }}
        onOk={handleSubmitEditBibleImage}
        confirmLoading={isBibleImageEditing}
        okText="开始改图"
        cancelText="取消"
        width={isMobile ? '100%' : 600}
      >
        {editingBibleImage && (
          <div style={{ textAlign: 'center', marginBottom: 16 }}>
            <Image
              src={buildAppPath(editingBibleImage.url)}
              alt={editingBibleImage.file_name}
              style={{ maxWidth: '100%', maxHeight: 300, borderRadius: 8 }}
            />
          </div>
        )}
        <div style={{ marginBottom: 8 }}>
          <Text type="secondary">输入改图提示词，AI 会基于当前圣经图片进行修改。描述你希望改变的部分。</Text>
        </div>
        <TextArea
          rows={6}
          value={editBibleImagePrompt}
          onChange={e => setEditBibleImagePrompt(e.target.value)}
          placeholder="描述你想要修改的内容，例如：将表情改为微笑、更换服装颜色、添加配饰..."
          maxLength={2000}
          showCount
        />
      </Modal>

      <Modal
        title="新增形象版本"
        open={isCreateVariantModalOpen}
        onCancel={() => setIsCreateVariantModalOpen(false)}
        onOk={handleCreateImageVariant}
        confirmLoading={isImageVariantCreating}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createVariantForm} layout="vertical">
          <Form.Item label="版本名称" name="variant_label" rules={[{ required: true, message: '请输入版本名称' }]}>
            <Input placeholder="如：第二卷赌石大会 / 筑基后期" />
          </Form.Item>
          <Form.Item label="版本类型" name="variant_type" rules={[{ required: true, message: '请选择版本类型' }]}>
            <Select>
              <Select.Option value="volume">分卷</Select.Option>
              <Select.Option value="period">时期/阶段</Select.Option>
            </Select>
          </Form.Item>
          <Row gutter={8}>
            <Col span={12}>
              <Form.Item label="起始章节" name="chapter_start">
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="结束章节" name="chapter_end">
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="版本提示词" name="prompt" extra="留空时会复制默认形象提示词，可创建后再细化。">
            <TextArea rows={6} placeholder="描述该版本的差异：年龄/发型/服装/气质/装备/伤痕等" maxLength={2000} showCount />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={editingCharacter?.is_organization ? '编辑组织' : '编辑角色'}
        open={isEditModalOpen}
        onCancel={() => {
          setIsEditModalOpen(false);
          editForm.resetFields();
          setEditingCharacter(null);
        }}
        footer={
          <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
            <Button onClick={() => {
              setIsEditModalOpen(false);
              editForm.resetFields();
              setEditingCharacter(null);
            }}>
              取消
            </Button>
            {editingCharacter && !editingCharacter.is_organization && characterImageStates[editingCharacter.id]?.has_image && (
              <Button
                icon={<PictureOutlined />}
                onClick={() => {
                  setIsEditModalOpen(false);
                  editForm.resetFields();
                  handleOpenCharacterImageModal(editingCharacter);
                  setEditingCharacter(null);
                }}
              >
                改图
              </Button>
            )}
            <Button type="primary" onClick={() => editForm.submit()}>
              保存
            </Button>
          </Space>
        }
        centered
        width={isMobile ? '100%' : 700}
        style={isMobile ? { top: 0, paddingBottom: 0, maxWidth: '100vw' } : undefined}
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 110px)' : 'calc(100vh - 200px)',
            overflowY: 'auto',
            overflowX: 'hidden'
          }
        }}
      >
        <Form form={editForm} layout="vertical" onFinish={handleUpdateCharacter} style={{ marginTop: 8 }}>
          {!editingCharacter?.is_organization ? (
            <>
              {/* 编辑角色 - 第一行：名称、定位、年龄、性别 */}
              <Row gutter={12}>
                <Col span={8}>
                  <Form.Item
                    label="角色名称"
                    name="name"
                    rules={[{ required: true, message: '请输入角色名称' }]}
                    style={{ marginBottom: 12 }}
                  >
                    <Input placeholder="角色名称" />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item label="角色定位" name="role_type" style={{ marginBottom: 12 }}>
                    <Select>
                      <Select.Option value="protagonist">主角</Select.Option>
                      <Select.Option value="supporting">配角</Select.Option>
                      <Select.Option value="antagonist">反派</Select.Option>
                    </Select>
                  </Form.Item>
                </Col>
                <Col span={5}>
                  <Form.Item label="年龄" name="age" style={{ marginBottom: 12 }}>
                    <Input placeholder="如：25岁" />
                  </Form.Item>
                </Col>
                <Col span={5}>
                  <Form.Item label="性别" name="gender" style={{ marginBottom: 12 }}>
                    <Select placeholder="性别">
                      <Select.Option value="男">男</Select.Option>
                      <Select.Option value="女">女</Select.Option>
                      <Select.Option value="其他">其他</Select.Option>
                    </Select>
                  </Form.Item>
                </Col>
              </Row>

              {/* 第二行：性格特点、外貌描写 */}
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item label="性格特点" name="personality" style={{ marginBottom: 12 }}>
                    <TextArea rows={2} placeholder="描述角色的性格特点..." />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="外貌描写" name="appearance" style={{ marginBottom: 12 }}>
                    <TextArea rows={2} placeholder="描述角色的外貌特征..." />
                  </Form.Item>
                </Col>
              </Row>

              {/* 人际关系（只读，由关系管理页面维护） */}
              {editingCharacter?.relationships && (
                <Form.Item label="人际关系（由关系管理维护）" style={{ marginBottom: 12 }}>
                  <Input.TextArea
                    value={editingCharacter.relationships}
                    readOnly
                    autoSize={{ minRows: 1, maxRows: 3 }}
                    style={{ backgroundColor: token.colorFillTertiary, cursor: 'default' }}
                  />
                </Form.Item>
              )}

              {/* 第四行：角色背景 */}
              <Form.Item label="角色背景" name="background" style={{ marginBottom: 12 }}>
                <TextArea rows={2} placeholder="描述角色的背景故事..." />
              </Form.Item>

              {/* 职业信息 */}
              {(mainCareers.length > 0 || subCareers.length > 0) && (
                <>
                  <Divider style={{ margin: '8px 0' }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>职业信息</Typography.Text>
                  </Divider>
                  {mainCareers.length > 0 && (
                    <Row gutter={12}>
                      <Col span={16}>
                        <Form.Item label="主职业" name="main_career_id" tooltip="角色的主要修炼职业" style={{ marginBottom: 12 }}>
                          <Select placeholder="选择主职业" allowClear size="small">
                            {mainCareers.map(career => (
                              <Select.Option key={career.id} value={career.id}>
                                {career.name}（最高{career.max_stage}阶）
                              </Select.Option>
                            ))}
                          </Select>
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item label="当前阶段" name="main_career_stage" tooltip="主职业当前修炼到的阶段" style={{ marginBottom: 12 }}>
                          <InputNumber
                            min={1}
                            max={editForm.getFieldValue('main_career_id') ?
                              mainCareers.find(c => c.id === editForm.getFieldValue('main_career_id'))?.max_stage || 10
                              : 10}
                            style={{ width: '100%' }}
                            placeholder="阶段"
                            size="small"
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                  )}
                  {subCareers.length > 0 && (
                    <Form.List name="sub_career_data">
                      {(fields, { add, remove }) => (
                        <>
                          <div style={{ marginBottom: 4 }}>
                            <Typography.Text strong style={{ fontSize: 12 }}>副职业</Typography.Text>
                          </div>
                          <div style={{ maxHeight: '80px', overflowY: 'auto', overflowX: 'hidden', marginBottom: 8, paddingRight: 8 }}>
                            {fields.map((field) => (
                              <Row key={field.key} gutter={8} style={{ marginBottom: 4 }}>
                                <Col span={16}>
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'career_id']}
                                    rules={[{ required: true, message: '请选择副职业' }]}
                                    style={{ marginBottom: 0 }}
                                  >
                                    <Select placeholder="选择副职业" size="small">
                                      {subCareers.map(career => (
                                        <Select.Option key={career.id} value={career.id}>
                                          {career.name}（最高{career.max_stage}阶）
                                        </Select.Option>
                                      ))}
                                    </Select>
                                  </Form.Item>
                                </Col>
                                <Col span={5}>
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'stage']}
                                    rules={[{ required: true, message: '阶段' }]}
                                    style={{ marginBottom: 0 }}
                                  >
                                    <InputNumber
                                      min={1}
                                      max={(() => {
                                        const careerId = editForm.getFieldValue(['sub_career_data', field.name, 'career_id']);
                                        const career = subCareers.find(c => c.id === careerId);
                                        return career?.max_stage || 10;
                                      })()}
                                      placeholder="阶段"
                                      style={{ width: '100%' }}
                                      size="small"
                                    />
                                  </Form.Item>
                                </Col>
                                <Col span={3}>
                                  <Button
                                    type="text"
                                    danger
                                    size="small"
                                    onClick={() => remove(field.name)}
                                  >
                                    删除
                                  </Button>
                                </Col>
                              </Row>
                            ))}
                          </div>
                          <Button
                            type="dashed"
                            onClick={() => add({ career_id: undefined, stage: 1 })}
                            block
                            size="small"
                          >
                            + 添加副职业
                          </Button>
                        </>
                      )}
                    </Form.List>
                  )}
                </>
              )}
            </>
          ) : (
            <>
              {/* 编辑组织 - 第一行：名称、类型、势力等级 */}
              <Row gutter={12}>
                <Col span={10}>
                  <Form.Item
                    label="组织名称"
                    name="name"
                    rules={[{ required: true, message: '请输入组织名称' }]}
                    style={{ marginBottom: 12 }}
                  >
                    <Input placeholder="组织名称" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item
                    label="组织类型"
                    name="organization_type"
                    rules={[{ required: true, message: '请输入组织类型' }]}
                    style={{ marginBottom: 12 }}
                  >
                    <Input placeholder="如：门派、帮派" />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item
                    label="势力等级"
                    name="power_level"
                    tooltip="0-100的数值"
                    style={{ marginBottom: 12 }}
                  >
                    <InputNumber min={0} max={100} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              {/* 第二行：组织目的 */}
              <Form.Item
                label="组织目的"
                name="organization_purpose"
                rules={[{ required: true, message: '请输入组织目的' }]}
                style={{ marginBottom: 12 }}
              >
                <Input placeholder="描述组织的宗旨和目标..." />
              </Form.Item>

              {/* 第三行：主要成员（只读展示） */}
              <Form.Item
                label="主要成员"
                name="organization_members"
                style={{ marginBottom: 4 }}
                tooltip="成员信息由组织管理模块维护，此处仅展示"
              >
                <TextArea
                  disabled
                  autoSize={{ minRows: 1, maxRows: 4 }}
                  placeholder="暂无成员，请在组织管理中添加"
                  style={{ color: token.colorText, backgroundColor: token.colorFillAlter }}
                />
              </Form.Item>
              <div style={{ marginBottom: 12, fontSize: 12, color: token.colorTextTertiary }}>
                💡 请前往「组织管理」页面添加或管理组织成员
              </div>

              {/* 第四行：所在地、代表颜色 */}
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item label="所在地" name="location" style={{ marginBottom: 12 }}>
                    <Input placeholder="总部位置" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="代表颜色" name="color" style={{ marginBottom: 12 }}>
                    <Input placeholder="如：金色" />
                  </Form.Item>
                </Col>
              </Row>

              {/* 第四行：格言/口号 */}
              <Form.Item label="格言/口号" name="motto" style={{ marginBottom: 12 }}>
                <Input placeholder="组织的宗旨、格言或口号" />
              </Form.Item>

              {/* 第五行：组织背景 */}
              <Form.Item label="组织背景" name="background" style={{ marginBottom: 12 }}>
                <TextArea rows={2} placeholder="描述组织的背景故事..." />
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>

      {/* 手动创建角色/组织模态框 */}
      <Modal
        title={createType === 'character' ? '创建角色' : '创建组织'}
        open={isCreateModalOpen}
        onCancel={() => {
          setIsCreateModalOpen(false);
          createForm.resetFields();
        }}
        footer={null}
        centered
        width={isMobile ? '100%' : 700}
        style={isMobile ? { top: 0, paddingBottom: 0, maxWidth: '100vw' } : undefined}
        styles={{
          body: {
            maxHeight: isMobile ? 'calc(100vh - 110px)' : 'calc(100vh - 200px)',
            overflowY: 'auto',
            overflowX: 'hidden'
          }
        }}
      >
        <Form form={createForm} layout="vertical" onFinish={handleCreateCharacter} style={{ marginTop: 8 }}>
          {createType === 'character' ? (
            <>
              {/* 角色基本信息 - 第一行：名称、定位、年龄、性别 */}
              <Row gutter={12}>
                <Col span={8}>
                  <Form.Item
                    label="角色名称"
                    name="name"
                    rules={[{ required: true, message: '请输入角色名称' }]}
                    style={{ marginBottom: 12 }}
                  >
                    <Input placeholder="角色名称" />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item label="角色定位" name="role_type" initialValue="supporting" style={{ marginBottom: 12 }}>
                    <Select>
                      <Select.Option value="protagonist">主角</Select.Option>
                      <Select.Option value="supporting">配角</Select.Option>
                      <Select.Option value="antagonist">反派</Select.Option>
                    </Select>
                  </Form.Item>
                </Col>
                <Col span={5}>
                  <Form.Item label="年龄" name="age" style={{ marginBottom: 12 }}>
                    <Input placeholder="如：25岁" />
                  </Form.Item>
                </Col>
                <Col span={5}>
                  <Form.Item label="性别" name="gender" style={{ marginBottom: 12 }}>
                    <Select placeholder="性别">
                      <Select.Option value="男">男</Select.Option>
                      <Select.Option value="女">女</Select.Option>
                      <Select.Option value="其他">其他</Select.Option>
                    </Select>
                  </Form.Item>
                </Col>
              </Row>

              {/* 第二行：性格特点、外貌描写 */}
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item label="性格特点" name="personality" style={{ marginBottom: 12 }}>
                    <TextArea rows={2} placeholder="描述角色的性格特点..." />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="外貌描写" name="appearance" style={{ marginBottom: 12 }}>
                    <TextArea rows={2} placeholder="描述角色的外貌特征..." />
                  </Form.Item>
                </Col>
              </Row>

              {/* 第三行：角色背景 */}
              <Form.Item label="角色背景" name="background" style={{ marginBottom: 12 }}>
                <TextArea rows={2} placeholder="描述角色的背景故事..." />
              </Form.Item>

              {/* 职业信息 - 折叠区域 */}
              {(mainCareers.length > 0 || subCareers.length > 0) && (
                <>
                  <Divider style={{ margin: '8px 0' }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>职业信息（可选）</Typography.Text>
                  </Divider>
                  {mainCareers.length > 0 && (
                    <Row gutter={12}>
                      <Col span={16}>
                        <Form.Item label="主职业" name="main_career_id" tooltip="角色的主要修炼职业" style={{ marginBottom: 12 }}>
                          <Select placeholder="选择主职业" allowClear size="small">
                            {mainCareers.map(career => (
                              <Select.Option key={career.id} value={career.id}>
                                {career.name}（最高{career.max_stage}阶）
                              </Select.Option>
                            ))}
                          </Select>
                        </Form.Item>
                      </Col>
                      <Col span={8}>
                        <Form.Item label="当前阶段" name="main_career_stage" tooltip="主职业当前修炼到的阶段" style={{ marginBottom: 12 }}>
                          <InputNumber
                            min={1}
                            max={createForm.getFieldValue('main_career_id') ?
                              mainCareers.find(c => c.id === createForm.getFieldValue('main_career_id'))?.max_stage || 10
                              : 10}
                            style={{ width: '100%' }}
                            placeholder="阶段"
                            size="small"
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                  )}
                  {subCareers.length > 0 && (
                    <Form.List name="sub_career_data">
                      {(fields, { add, remove }) => (
                        <>
                          <div style={{ marginBottom: 4 }}>
                            <Typography.Text strong style={{ fontSize: 12 }}>副职业</Typography.Text>
                          </div>
                          <div style={{ maxHeight: '80px', overflowY: 'auto', overflowX: 'hidden', marginBottom: 8, paddingRight: 8 }}>
                            {fields.map((field) => (
                              <Row key={field.key} gutter={8} style={{ marginBottom: 4 }}>
                                <Col span={16}>
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'career_id']}
                                    rules={[{ required: true, message: '请选择副职业' }]}
                                    style={{ marginBottom: 0 }}
                                  >
                                    <Select placeholder="选择副职业" size="small">
                                      {subCareers.map(career => (
                                        <Select.Option key={career.id} value={career.id}>
                                          {career.name}（最高{career.max_stage}阶）
                                        </Select.Option>
                                      ))}
                                    </Select>
                                  </Form.Item>
                                </Col>
                                <Col span={5}>
                                  <Form.Item
                                    {...field}
                                    name={[field.name, 'stage']}
                                    rules={[{ required: true, message: '阶段' }]}
                                    style={{ marginBottom: 0 }}
                                  >
                                    <InputNumber
                                      min={1}
                                      max={(() => {
                                        const careerId = createForm.getFieldValue(['sub_career_data', field.name, 'career_id']);
                                        const career = subCareers.find(c => c.id === careerId);
                                        return career?.max_stage || 10;
                                      })()}
                                      placeholder="阶段"
                                      style={{ width: '100%' }}
                                      size="small"
                                    />
                                  </Form.Item>
                                </Col>
                                <Col span={3}>
                                  <Button
                                    type="text"
                                    danger
                                    size="small"
                                    onClick={() => remove(field.name)}
                                  >
                                    删除
                                  </Button>
                                </Col>
                              </Row>
                            ))}
                          </div>
                          <Button
                            type="dashed"
                            onClick={() => add({ career_id: undefined, stage: 1 })}
                            block
                            size="small"
                          >
                            + 添加副职业
                          </Button>
                        </>
                      )}
                    </Form.List>
                  )}
                </>
              )}
            </>
          ) : (
            <>
              {/* 组织基本信息 - 第一行：名称、类型、势力等级 */}
              <Row gutter={12}>
                <Col span={10}>
                  <Form.Item
                    label="组织名称"
                    name="name"
                    rules={[{ required: true, message: '请输入组织名称' }]}
                    style={{ marginBottom: 12 }}
                  >
                    <Input placeholder="组织名称" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item
                    label="组织类型"
                    name="organization_type"
                    rules={[{ required: true, message: '请输入组织类型' }]}
                    style={{ marginBottom: 12 }}
                  >
                    <Input placeholder="如：门派、帮派" />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item
                    label="势力等级"
                    name="power_level"
                    initialValue={50}
                    tooltip="0-100的数值"
                    style={{ marginBottom: 12 }}
                  >
                    <InputNumber min={0} max={100} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              {/* 第二行：组织目的 */}
              <Form.Item
                label="组织目的"
                name="organization_purpose"
                rules={[{ required: true, message: '请输入组织目的' }]}
                style={{ marginBottom: 12 }}
              >
                <Input placeholder="描述组织的宗旨和目标..." />
              </Form.Item>

              {/* 第三行：所在地、代表颜色 */}
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item label="所在地" name="location" style={{ marginBottom: 12 }}>
                    <Input placeholder="总部位置" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="代表颜色" name="color" style={{ marginBottom: 12 }}>
                    <Input placeholder="如：金色" />
                  </Form.Item>
                </Col>
              </Row>

              {/* 第四行：格言/口号 */}
              <Form.Item label="格言/口号" name="motto" style={{ marginBottom: 12 }}>
                <Input placeholder="组织的宗旨、格言或口号" />
              </Form.Item>

              {/* 第五行：组织背景 */}
              <Form.Item label="组织背景" name="background" style={{ marginBottom: 12 }}>
                <TextArea rows={2} placeholder="描述组织的背景故事..." />
              </Form.Item>
            </>
          )}

          <Form.Item style={{ marginBottom: 0, marginTop: 16 }}>
            <Space style={{ width: '100%', justifyContent: 'flex-end' }}>
              <Button onClick={() => {
                setIsCreateModalOpen(false);
                createForm.resetFields();
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit">
                创建
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 导入对话框 */}
      <Modal
        title="导入角色/组织"
        open={isImportModalOpen}
        onCancel={() => setIsImportModalOpen(false)}
        footer={null}
        width={500}
        centered
      >
        <div style={{ textAlign: 'center', padding: '40px 20px' }}>
          <DownloadOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 16 }} />
          <p style={{ fontSize: 16, marginBottom: 24 }}>
            选择之前导出的角色/组织JSON文件进行导入
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".json"
            style={{ display: 'none' }}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) {
                handleFileSelect(file);
                e.target.value = ''; // 清空input，允许重复选择同一文件
              }
            }}
          />
          <Button
            type="primary"
            size="large"
            icon={<ImportOutlined />}
            onClick={() => fileInputRef.current?.click()}
          >
            选择文件
          </Button>
          <Divider />
          <div style={{ textAlign: 'left', fontSize: 12, color: '#666' }}>
            <p style={{ marginBottom: 8 }}><strong>说明：</strong></p>
            <ul style={{ marginLeft: 20 }}>
              <li>支持导入.json格式的角色/组织文件</li>
              <li>重复名称的角色/组织将被跳过</li>
              <li>职业信息如不存在将被忽略</li>
            </ul>
          </div>
        </div>
      </Modal>

      {/* SSE进度显示 */}
      <SSELoadingOverlay
        loading={isGenerating}
        progress={progress}
        message={progressMessage}
      />
    </div>
  );
}
