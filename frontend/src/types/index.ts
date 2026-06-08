// 用户类型定义
export interface User {
  user_id: string;
  username: string;
  display_name: string;
  avatar_url?: string;
  trust_level: number;
  is_admin: boolean;
  linuxdo_id: string;
  created_at: string;
  last_login: string;
}

export interface EmailLoginPayload {
  email: string;
  code: string;
}

export interface EmailRegisterPayload {
  email: string;
  code: string;
  password: string;
  display_name?: string;
}

export interface EmailSendCodePayload {
  email: string;
  scene: 'register' | 'login' | 'reset_password';
}

export interface EmailResetPasswordPayload {
  email: string;
  code: string;
  new_password: string;
}

export interface SystemSMTPSettings {
  id: string;
  user_id: string;
  smtp_provider: string;
  smtp_host?: string;
  smtp_port: number;
  smtp_username?: string;
  smtp_password?: string;
  smtp_use_tls: boolean;
  smtp_use_ssl: boolean;
  smtp_from_email?: string;
  smtp_from_name: string;
  email_auth_enabled: boolean;
  email_register_enabled: boolean;
  verification_code_ttl_minutes: number;
  verification_resend_interval_seconds: number;
  created_at: string;
  updated_at: string;
}

export interface SystemSMTPSettingsUpdate {
  smtp_provider?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_username?: string;
  smtp_password?: string;
  smtp_use_tls?: boolean;
  smtp_use_ssl?: boolean;
  smtp_from_email?: string;
  smtp_from_name?: string;
  email_auth_enabled?: boolean;
  email_register_enabled?: boolean;
  verification_code_ttl_minutes?: number;
  verification_resend_interval_seconds?: number;
}

// 设置类型定义
export interface Settings {
  id: string;
  user_id: string;
  api_provider: string;
  api_key: string;
  api_base_url: string;
  llm_model: string;
  temperature: number;
  max_tokens: number;
  system_prompt?: string;
  cover_api_provider?: string;
  cover_api_key?: string;
  cover_api_base_url?: string;
  cover_image_model?: string;
  cover_enabled?: boolean;
  image_text_language?: 'zh' | 'en';
  preferences?: string;
  created_at: string;
  updated_at: string;
}

export interface SettingsUpdate {
  api_provider?: string;
  api_key?: string;
  api_base_url?: string;
  llm_model?: string;
  temperature?: number;
  max_tokens?: number;
  system_prompt?: string;
  cover_api_provider?: string;
  cover_api_key?: string;
  cover_api_base_url?: string;
  cover_image_model?: string;
  cover_enabled?: boolean;
  image_text_language?: 'zh' | 'en';
  preferences?: string;
}

// API预设相关类型定义
export interface APIKeyPresetConfig {
  api_provider: string;
  api_key: string;
  api_base_url?: string;
  llm_model: string;
  temperature: number;
  max_tokens: number;
  system_prompt?: string;
}

export interface APIKeyPreset {
  id: string;
  name: string;
  description?: string;
  is_active: boolean;
  created_at: string;
  config: APIKeyPresetConfig;
}

export interface PresetCreateRequest {
  name: string;
  description?: string;
  config: APIKeyPresetConfig;
}

export interface PresetUpdateRequest {
  name?: string;
  description?: string;
  config?: APIKeyPresetConfig;
}

export interface PresetListResponse {
  presets: APIKeyPreset[];
  total: number;
  active_preset_id?: string;
}

// LinuxDO 授权 URL 响应
export interface AuthUrlResponse {
  auth_url: string;
  state: string;
}

// 项目类型定义
export interface Project {
  id: string;  // UUID字符串
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  current_words: number;
  status: 'planning' | 'writing' | 'revising' | 'completed';
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
  outline_mode: 'one-to-one' | 'one-to-many';  // 大纲章节模式
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
  chapter_count?: number;
  narrative_perspective?: string;
  character_count?: number;
  comic_style?: string;
  comic_style_prompt?: string | null;
  cover_image_url?: string;
  cover_prompt?: string;
  cover_status?: 'none' | 'generating' | 'ready' | 'failed';
  cover_error?: string;
  cover_updated_at?: string;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreate {
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  comic_style?: string;
  comic_style_prompt?: string | null;
  outline_mode?: 'one-to-one' | 'one-to-many';  // 大纲章节模式,默认one-to-many
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
}

export interface ProjectUpdate {
  title?: string;
  description?: string;
  theme?: string;
  genre?: string;
  target_words?: number;
  status?: 'planning' | 'writing' | 'revising' | 'completed';
  world_time_period?: string;
  world_location?: string;
  world_atmosphere?: string;
  world_rules?: string;
  chapter_count?: number;
  narrative_perspective?: string;
  character_count?: number;
  comic_style?: string;
  comic_style_prompt?: string | null;
  // current_words 由章节内容自动计算，不在此接口中
}

// 向导专用的项目更新接口，包含向导流程控制字段
export interface ProjectWizardUpdate extends ProjectUpdate {
  wizard_status?: 'incomplete' | 'completed';
  wizard_step?: number;
}

// 项目创建向导
export interface ProjectWizardRequest {
  title: string;
  theme: string;
  genre?: string;
  chapter_count: number;
  narrative_perspective: string;
  character_count?: number;
  comic_style?: string;
  comic_style_prompt?: string | null;
  target_words?: number;
  outline_mode?: 'one-to-one' | 'one-to-many';  // 大纲章节模式
  world_building?: {
    time_period: string;
    location: string;
    atmosphere: string;
    rules: string;
  };
}

export interface WorldBuildingResponse {
  project_id: string;
  time_period: string;
  location: string;
  atmosphere: string;
  rules: string;
  comic_style?: string;
  comic_style_prompt?: string | null;
}

// 大纲类型定义
export interface Outline {
  id: string;
  project_id: string;
  title: string;
  content: string;
  structure?: string;
  order_index: number;
  has_chapters?: boolean;
  created_at: string;
  updated_at: string;
}

export interface OutlineCreate {
  project_id: string;
  title: string;
  content: string;
  structure?: string;
  order_index: number;
}

export interface OutlineUpdate {
  title?: string;
  content?: string;
  structure?: string;  // 支持修改structure字段
  // order_index 只能通过 reorder 接口批量调整
}

// 角色类型定义
export interface Character {
  id: string;
  project_id: string;
  name: string;
  age?: string;
  gender?: string;
  is_organization: boolean;
  role_type?: string;
  personality?: string;
  background?: string;
  appearance?: string;
  relationships?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  traits?: string;
  avatar_url?: string;
  // 组织扩展字段（从Organization表关联）
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
  // 角色/组织状态
  status?: string;
  status_changed_chapter?: number;
  current_state?: string;
  state_updated_chapter?: number;
  // 职业相关字段
  main_career_id?: string;
  main_career_stage?: number;
  sub_careers?: Array<{
    career_id: string;
    stage: number;
  }>;
  // 角色视觉圣经
  visual_bible?: {
    trigger_token?: string;
    immutable_traits?: Record<string, string>;
    forbidden_traits?: string[];
    views?: Array<{ angle: string; description: string }>;
    expressions?: Array<{ name: string; description: string }>;
    outfits?: Array<{ name: string; description: string }>;
    training_caption?: string;
  } | null;
  created_at: string;
  updated_at: string;
}

export type CharacterImageStatus =
  | 'none'
  | 'generating'
  | 'ready'
  | 'capacity'
  | 'policy'
  | 'failed';

export type CharacterImageVariantType = 'default' | 'volume' | 'period';

export interface CharacterImageState {
  character_id: string;
  project_id: string;
  name: string;
  variant_key: string;
  variant_label: string;
  variant_type: CharacterImageVariantType | string;
  chapter_start?: number | null;
  chapter_end?: number | null;
  sort_order?: number;
  variant_count?: number;
  prompt: string;
  image_url?: string;
  status: CharacterImageStatus | string;
  updated_at?: string;
  error?: string;
  error_type?: string;
  file_name?: string;
  has_image: boolean;
}

export interface CharacterImageActionResponse extends CharacterImageState {
  message: string;
  task_id?: string | null;
  queued?: boolean;
}

export interface CharacterImageVariantListResponse {
  character_id: string;
  project_id: string;
  name: string;
  total: number;
  items: CharacterImageState[];
}

export interface CharacterImageVariantCreateRequest {
  variant_label: string;
  variant_type: Exclude<CharacterImageVariantType, 'default'>;
  chapter_start?: number | null;
  chapter_end?: number | null;
  prompt?: string;
}

export interface CharacterImageVariantUpdateRequest {
  variant_label?: string;
  variant_type?: CharacterImageVariantType;
  chapter_start?: number | null;
  chapter_end?: number | null;
  prompt?: string;
}

export interface CharacterImageInitializeResponse {
  project_id: string;
  total_candidates: number;
  character_candidates?: number;
  organization_candidates?: number;
  generated: number;
  skipped: number;
  failed: number;
  character_processed?: number;
  organization_processed?: number;
  items: CharacterImageActionResponse[];
}

export interface BibleImageItem {
  file_name: string;
  url: string;
  angle: string;
  expression: string;
  outfit: string;
}

export interface BibleImageListResponse {
  character_id: string;
  images: BibleImageItem[];
  task?: {
    status: string;
    total: number;
    completed: number;
    failed: number;
  } | null;
}

export interface CharacterUpdate {
  name?: string;
  age?: string;
  gender?: string;
  is_organization?: boolean;
  role_type?: string;
  personality?: string;
  background?: string;
  appearance?: string;
  organization_type?: string;
  organization_purpose?: string;
  organization_members?: string;
  traits?: string;
  // 组织扩展字段
  power_level?: number;
  location?: string;
  motto?: string;
  color?: string;
}

// 展开规划数据结构
export interface ExpansionPlanData {
  key_events: string[];
  character_focus: string[];
  emotional_tone: string;
  narrative_goal: string;
  conflict_type: string;
  estimated_words: number;
  scenes?: Array<{
    location: string;
    characters: string[];
    purpose: string;
  }> | null;
}

// 章节类型定义
export interface Chapter {
  id: string;
  project_id: string;
  title: string;
  content?: string;
  summary?: string;
  chapter_number: number;
  word_count: number;
  status: 'draft' | 'writing' | 'completed';
  expansion_plan?: string; // JSON字符串，解析后为ExpansionPlanData
  outline_id?: string; // 关联的大纲ID
  sub_index?: number; // 大纲下的子章节序号
  outline_title?: string; // 大纲标题（从后端联表查询获得）
  outline_order?: number; // 大纲排序序号（从后端联表查询获得）
  created_at: string;
  updated_at: string;
}

export interface ChapterCreate {
  project_id: string;
  title: string;
  chapter_number: number;
  content?: string;
  summary?: string;
  status?: 'draft' | 'writing' | 'completed';
}

export interface ChapterUpdate {
  title?: string;
  content?: string;
  // chapter_number 不允许修改，由大纲顺序决定
  summary?: string;
  // word_count 自动计算，不允许手动修改
  status?: 'draft' | 'writing' | 'completed';
}

// 章节生成请求类型
export interface ChapterGenerateRequest {
  style_id?: number;
  target_word_count?: number;
}

// 章节生成检查响应
export interface ChapterCanGenerateResponse {
  can_generate: boolean;
  reason: string;
  previous_chapters: {
    id: string;
    chapter_number: number;
    title: string;
    has_content: boolean;
    word_count: number;
  }[];
  chapter_number: number;
}

export interface ComicStoryboardState {
  exists?: boolean;
  status?: string | null;
  json_path?: string | null;
  markdown_path?: string | null;
  updated_at?: string | null;
  mtime?: string | null;
  json_text?: string | null;
  json_content?: unknown;
  markdown_content?: string | null;
  page_count?: number | null;
  panel_count?: number | null;
}

export interface ComicPage {
  page_number: number;
  status: string;
  image_available: boolean;
  image_url?: string | null;
  prompt_path?: string | null;
  file_path?: string | null;
  failed: boolean;
  failed_metadata?: Record<string, unknown> | null;
  regeneration?: Record<string, unknown> | null;
  error_message?: string | null;
  updated_at?: string | null;
  mtime?: string | null;
}

export interface ComicProjectChapterStatus {
  chapter_number: number;
  chapter_id?: string | null;
  chapter_title?: string | null;
  chapter_status: string;
  storyboard: ComicStoryboardState;
  page_count: number;
  available_page_count: number;
  pages: ComicPage[];
  failed_page_numbers: number[];
  updated_at?: string | null;
  mtime?: string | null;
}

export interface ComicProjectResponse {
  project_id: string;
  chapters: ComicProjectChapterStatus[];
  summary: {
    chapter_count: number;
    storyboard_count: number;
    image_page_count: number;
    failed_page_count: number;
  };
}

export interface ComicChapterCombinedResponse {
  project_id: string;
  chapter_number: number;
  chapter: {
    id?: string | null;
    number: number;
    title?: string | null;
    content?: string | null;
    summary?: string | null;
    status?: string | null;
    word_count?: number | null;
    updated_at?: string | null;
  };
  storyboard: {
    json_text?: string | null;
    json_content?: unknown;
    markdown_content?: string | null;
    status?: string | null;
    updated_at?: string | null;
  };
  comic: {
    pages: ComicPage[];
    page_count: number;
    available_page_count: number;
    chapter_status: string;
  };
}

export interface ComicChapterRegenerationStatusResponse {
  project_id: string;
  chapter_number: number;
  chapter_id?: string | null;
  chapter_title?: string | null;
  chapter_status: string;
  page_count: number;
  available_page_count: number;
  queued_page_count: number;
  running_page_count: number;
  failed_page_count: number;
  completed_page_count: number;
  pages: ComicPage[];
  updated_at?: string | null;
  mtime?: string | null;
}

export interface ComicBatchGenerateResponse {
  task_id: string;
  status: string;
  total: number;
  chapter_numbers: number[];
  message: string;
}

export interface ComicBatchGenerateStatusResponse {
  task_id: string;
  project_id: string;
  type: string;
  status: string;
  total: number;
  completed: number;
  current_chapter_number: number | null;
  chapter_numbers: number[];
  errors?: Array<{ chapter_number: number; error: string }>;
  skipped_chapters?: Array<{ chapter_number: number; reason: string }>;
  chapter_results?: Array<Record<string, unknown>>;
  error?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ComicPipelineStageStatusResponse {
  total: number;
  processed: number;
  succeeded: number;
  failed: number;
  current_chapter_number?: number | null;
  current_retry_count?: number | null;
  error_message?: string | null;
}

export type ComicFullPipelineGenerationMode = 'full' | 'incremental';

export interface ComicFullPipelineBatchGenerateRequest {
  start_chapter_number: number;
  count: number;
  style_id?: number;
  target_word_count?: number;
  enable_analysis?: boolean;
  enable_mcp?: boolean;
  max_retries?: number;
  model?: string;
  target_pages?: number;
  comic_page_concurrency?: number;
  generation_mode?: ComicFullPipelineGenerationMode;
}

export interface ComicFullPipelineBatchGenerateResponse {
  task_id: string;
  status: string;
  generation_mode: ComicFullPipelineGenerationMode;
  total: number;
  chapter_numbers: number[];
  message: string;
}

export interface ComicFullPipelineBatchStatusResponse {
  task_id: string;
  project_id: string;
  status: string;
  generation_mode?: ComicFullPipelineGenerationMode | null;
  current_stage?: string | null;
  total: number;
  completed: number;
  successful: number;
  failed: number;
  chapter_numbers: number[];
  current_chapter_number?: number | null;
  current_retry_count?: number | null;
  stages: Record<string, ComicPipelineStageStatusResponse>;
  errors?: Array<Record<string, unknown>>;
  chapter_results?: Array<Record<string, unknown>>;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

export interface ComicReadingChapter {
  chapter_number: number;
  chapter_id?: string | null;
  chapter_title?: string | null;
  chapter_status?: string | null;
  page_count: number;
  updated_at?: string | null;
  pages: Array<ComicPage & {
    chapter_number: number;
    chapter_title?: string | null;
  }>;
}

export interface ComicContinuousReadResponse {
  project_id: string;
  chapters: ComicReadingChapter[];
  pages: Array<ComicPage & {
    chapter_number: number;
    chapter_title?: string | null;
  }>;
  summary: {
    chapter_count: number;
    page_count: number;
  };
  updated_at?: string | null;
}

// AI生成请求类型
export interface GenerateOutlineRequest {
  project_id: string;
  genre?: string;
  theme: string;
  chapter_count: number;
  narrative_perspective: string;
  world_context?: Record<string, unknown>;
  characters_context?: Character[];
  target_words?: number;
  requirements?: string;
  provider?: string;
  model?: string;
  // 续写功能新增字段
  mode?: 'auto' | 'new' | 'continue';
  story_direction?: string;
  plot_stage?: 'development' | 'climax' | 'ending';
  keep_existing?: boolean;
}

// 大纲重排序请求类型
export interface OutlineReorderItem {
  id: string;
  order_index: number;
}

export interface OutlineReorderRequest {
  orders: OutlineReorderItem[];
}

// 大纲展开相关类型定义
export interface ChapterPlanItem {
  sub_index: number;
  title: string;
  plot_summary: string;
  key_events: string[];
  character_focus: string[];
  emotional_tone: string;
  narrative_goal: string;
  conflict_type: string;
  estimated_words: number;
  scenes?: Array<{
    location: string;
    characters: string[];
    purpose: string;
  }>;
}

export interface OutlineExpansionRequest {
  target_chapter_count: number;
  expansion_strategy?: 'balanced' | 'climax' | 'detail';
  auto_create_chapters?: boolean;
  provider?: string;
  model?: string;
}

export interface OutlineExpansionResponse {
  outline_id: string;
  outline_title: string;
  target_chapter_count: number;
  actual_chapter_count: number;
  expansion_strategy: string;
  chapter_plans: ChapterPlanItem[];
  created_chapters?: Array<{
    id: string;
    chapter_number: number;
    title: string;
    summary: string;
    outline_id: string;
    sub_index: number;
    status: string;
  }> | null;
}

export interface BatchOutlineExpansionRequest {
  project_id: string;
  outline_ids?: string[];
  chapters_per_outline: number;
  expansion_strategy?: 'balanced' | 'climax' | 'detail';
  auto_create_chapters?: boolean;
  provider?: string;
  model?: string;
}

export interface BatchOutlineExpansionResponse {
  project_id: string;
  total_outlines_expanded: number;
  total_chapters_created: number;
  expansion_results: OutlineExpansionResponse[];
  skipped_outlines?: Array<{
    outline_id: string;
    outline_title: string;
    reason: string;
  }>;
}

export interface BatchOutlineExpansionTaskCreateResponse {
  task_id: string;
  status: 'pending' | 'running' | 'completed' | 'partial_failed' | 'failed';
  message: string;
}

export interface BatchOutlineExpansionTaskStatus {
  task_id: string;
  project_id: string;
  status: 'pending' | 'running' | 'completed' | 'partial_failed' | 'failed';
  progress: number;
  message: string;
  total: number;
  completed: number;
  skipped_count: number;
  failed_count: number;
  total_chapters_created: number;
  current_outline_id?: string;
  current_outline_title?: string;
  skipped_outlines?: Array<{
    outline_id: string;
    outline_title: string;
    reason: string;
  }>;
  failed_outlines?: Array<{
    outline_id: string;
    outline_title: string;
    error: string;
  }>;
}

export interface GenerateCharacterRequest {
  project_id: string;
  name?: string;
  role_type?: string;
  background?: string;
  requirements?: string;
  provider?: string;
  model?: string;
}

export interface PolishTextRequest {
  text: string;
  style?: string;
}

// 向导API响应类型
export interface GenerateCharactersResponse {
  characters: Character[];
}

export interface GenerateOutlineResponse {
  outlines: Outline[];
}

// API响应类型
export interface ApiResponse<T> {
  data: T;
  message?: string;
}

// 写作风格类型定义
export interface WritingStyle {
  id: number;
  user_id: string | null;  // NULL 表示全局预设风格
  name: string;
  style_type: 'preset' | 'custom';
  preset_id?: string;
  description?: string;
  prompt_content: string;
  is_default: boolean;
  order_index: number;
  created_at: string;
  updated_at: string;
}

export interface WritingStyleCreate {
  name: string;
  style_type?: 'preset' | 'custom';
  preset_id?: string;
  description?: string;
  prompt_content: string;
}

export interface WritingStyleUpdate {
  name?: string;
  description?: string;
  prompt_content?: string;
  order_index?: number;
}

export interface PresetStyle {
  id: string;
  name: string;
  description: string;
  prompt_content: string;
}

export interface WritingStyleListResponse {
  styles: WritingStyle[];
  total: number;
}

export interface PaginationResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

// 向导表单数据类型
export interface WizardBasicInfo {
  title: string;
  description: string;
  theme: string;
  genre: string | string[];
  chapter_count: number;
  narrative_perspective: string;
  character_count?: number;
  target_words?: number;
  comic_style?: string;
  comic_style_prompt?: string | null;
  outline_mode?: 'one-to-one' | 'one-to-many';  // 大纲章节模式
}

// API 错误响应类型
export interface ApiError {
  response?: {
    data?: {
      detail?: string;
    };
  };
  message?: string;
}

// 章节分析任务相关类型
export interface AnalysisTask {
  has_task: boolean;
  task_id: string | null;
  chapter_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'none';
  progress: number;
  error_message?: string | null;
  auto_recovered?: boolean;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface BatchAnalysisStatusResponse {
  project_id: string;
  total: number;
  items: Record<string, AnalysisTask>;
}

export interface BatchAnalyzeUnanalyzedRequest {
  chapter_ids?: string[];
}

export interface BatchAnalyzeUnanalyzedResponse {
  project_id: string;
  total_candidates: number;
  total_started: number;
  total_skipped_no_content: number;
  total_skipped_running: number;
  total_already_completed: number;
  started_tasks: Record<string, AnalysisTask>;
}

// 分析结果 - 钩子
export interface AnalysisHook {
  type: string;
  content: string;
  strength: number;
  position: string;
}

// 分析结果 - 伏笔
export interface AnalysisForeshadow {
  content: string;
  type: 'planted' | 'resolved';
  strength: number;
  subtlety: number;
  reference_chapter?: number;
}

// 分析结果 - 冲突
export interface AnalysisConflict {
  types: string[];
  parties: string[];
  level: number;
  description: string;
  resolution_progress: number;
}

// 分析结果 - 情感曲线
export interface AnalysisEmotionalArc {
  primary_emotion: string;
  intensity: number;
  curve: string;
  secondary_emotions: string[];
}

// 分析结果 - 角色状态
export interface AnalysisCharacterState {
  character_name: string;
  state_before: string;
  state_after: string;
  psychological_change: string;
  key_event: string;
  relationship_changes: Record<string, string>;
}

// 分析结果 - 情节点
export interface AnalysisPlotPoint {
  content: string;
  type: 'revelation' | 'conflict' | 'resolution' | 'transition';
  importance: number;
  impact: string;
}

// 分析结果 - 场景
export interface AnalysisScene {
  location: string;
  atmosphere: string;
  duration: string;
}

// 分析结果 - 评分
export interface AnalysisScores {
  pacing: number;
  engagement: number;
  coherence: number;
  overall: number;
}

// 完整分析数据 - 匹配后端PlotAnalysis模型
export interface AnalysisData {
  id: string;
  chapter_id: string;
  plot_stage: string;
  conflict_level: number;
  conflict_types: string[];
  emotional_tone: string;
  emotional_intensity: number;
  hooks: AnalysisHook[];
  hooks_count: number;
  foreshadows: AnalysisForeshadow[];
  foreshadows_planted: number;
  foreshadows_resolved: number;
  plot_points: AnalysisPlotPoint[];
  plot_points_count: number;
  character_states: AnalysisCharacterState[];
  scenes?: AnalysisScene[];
  pacing: string;
  overall_quality_score: number;
  pacing_score: number;
  engagement_score: number;
  coherence_score: number;
  analysis_report: string;
  suggestions: string[];
  dialogue_ratio: number;
  description_ratio: number;
  created_at: string;
}

// 记忆片段
export interface StoryMemory {
  id: string;
  type: 'hook' | 'foreshadow' | 'plot_point' | 'character_event';
  title: string;
  content: string;
  importance: number;
  tags: string[];
  is_foreshadow: 0 | 1 | 2; // 0=普通, 1=已埋下, 2=已回收
}

export interface EntityChangesSummaryItem {
  updated_count?: number;
  state_updated_count?: number;
  relationship_created_count?: number;
  relationship_updated_count?: number;
  org_updated_count?: number;
  changes: string[];
}

// 章节分析结果响应 - 匹配后端API返回
export interface ChapterAnalysisResponse {
  chapter_id: string;
  analysis: AnalysisData;  // 注意：后端返回的是analysis而不是analysis_data
  memories: StoryMemory[];
  created_at: string;
  entity_changes?: {
    careers: EntityChangesSummaryItem;
    character_states: EntityChangesSummaryItem;
    organization_states: EntityChangesSummaryItem;
  };
}

// 手动触发分析响应
export interface TriggerAnalysisResponse {
  task_id: string;
  chapter_id: string;
  status: string;
  message: string;
}

// MCP 插件类型定义 - 优化后只包含必要字段
export interface MCPPlugin {
  id: string;
  plugin_name: string;
  display_name: string;
  description?: string;
  plugin_type: 'http' | 'stdio' | 'streamable_http' | 'sse';
  category: string;

  // HTTP类型字段
  server_url?: string;
  headers?: Record<string, string>;

  // Stdio类型字段
  command?: string;
  args?: string[];
  env?: Record<string, string>;

  // 状态字段
  enabled: boolean;
  status: 'active' | 'inactive' | 'error';
  last_error?: string;
  last_test_at?: string;

  // 时间戳
  created_at: string;
}

export interface MCPPluginCreate {
  plugin_name: string;
  display_name?: string;
  description?: string;
  server_type: 'http' | 'stdio' | 'streamable_http' | 'sse';
  server_url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface MCPPluginUpdate {
  display_name?: string;
  description?: string;
  server_url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  enabled?: boolean;
}

export interface MCPTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

export interface MCPTestResult {
  success: boolean;
  message: string;
  tools?: MCPTool[];
  tools_count?: number;
  response_time_ms?: number;
  error?: string;
  error_type?: string;
  suggestions?: string[];
}

export interface MCPToolCallRequest {
  plugin_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

export interface MCPToolCallResponse {
  success: boolean;
  result?: unknown;
  error?: string;
}

// 伏笔管理类型定义
export type ForeshadowStatus = 'pending' | 'planted' | 'resolved' | 'partially_resolved' | 'abandoned';
export type ForeshadowSourceType = 'analysis' | 'manual';
export type ForeshadowCategory = 'identity' | 'mystery' | 'item' | 'relationship' | 'event' | 'ability' | 'prophecy';

export interface Foreshadow {
  id: string;
  project_id: string;
  title: string;
  content: string;
  hint_text?: string;
  resolution_text?: string;
  source_type?: ForeshadowSourceType;
  source_memory_id?: string;
  source_analysis_id?: string;
  plant_chapter_id?: string;
  plant_chapter_number?: number;
  target_resolve_chapter_id?: string;
  target_resolve_chapter_number?: number;
  actual_resolve_chapter_id?: string;
  actual_resolve_chapter_number?: number;
  status: ForeshadowStatus;
  is_long_term: boolean;
  importance: number;
  strength: number;
  subtlety: number;
  urgency: number;
  related_characters?: string[];
  related_foreshadow_ids?: string[];
  tags?: string[];
  category?: ForeshadowCategory;
  notes?: string;
  resolution_notes?: string;
  auto_remind: boolean;
  remind_before_chapters: number;
  include_in_context: boolean;
  created_at?: string;
  updated_at?: string;
  planted_at?: string;
  resolved_at?: string;
}

export interface ForeshadowCreate {
  project_id: string;
  title: string;
  content: string;
  hint_text?: string;
  resolution_text?: string;
  plant_chapter_number?: number;
  target_resolve_chapter_number?: number;
  is_long_term?: boolean;
  importance?: number;
  strength?: number;
  subtlety?: number;
  related_characters?: string[];
  tags?: string[];
  category?: ForeshadowCategory;
  notes?: string;
  resolution_notes?: string;
  auto_remind?: boolean;
  remind_before_chapters?: number;
  include_in_context?: boolean;
}

export interface ForeshadowUpdate {
  title?: string;
  content?: string;
  hint_text?: string;
  resolution_text?: string;
  plant_chapter_number?: number;
  target_resolve_chapter_number?: number;
  status?: ForeshadowStatus;
  is_long_term?: boolean;
  importance?: number;
  strength?: number;
  subtlety?: number;
  urgency?: number;
  related_characters?: string[];
  related_foreshadow_ids?: string[];
  tags?: string[];
  category?: ForeshadowCategory;
  notes?: string;
  resolution_notes?: string;
  auto_remind?: boolean;
  remind_before_chapters?: number;
  include_in_context?: boolean;
}

export interface ForeshadowStats {
  total: number;
  pending: number;
  planted: number;
  resolved: number;
  partially_resolved: number;
  abandoned: number;
  long_term_count: number;
  overdue_count: number;
}

export interface ForeshadowListResponse {
  total: number;
  items: Foreshadow[];
  stats?: ForeshadowStats;
}

export interface PlantForeshadowRequest {
  chapter_id: string;
  chapter_number: number;
  hint_text?: string;
}

export interface ResolveForeshadowRequest {
  chapter_id: string;
  chapter_number: number;
  resolution_text?: string;
  is_partial?: boolean;
}

export interface SyncFromAnalysisRequest {
  chapter_ids?: string[];
  overwrite_existing?: boolean;
  auto_set_planted?: boolean;
}

export interface SyncFromAnalysisResponse {
  synced_count: number;
  skipped_count: number;
  new_foreshadows: Foreshadow[];
  skipped_reasons: Array<{ source_memory_id: string; reason: string }>;
}

export interface ForeshadowContextResponse {
  chapter_number: number;
  context_text: string;
  pending_plant: Foreshadow[];
  pending_resolve: Foreshadow[];
  overdue: Foreshadow[];
  recently_planted: Foreshadow[];
}

// ==================== 拆书导入类型定义 ====================

export type BookImportTaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export type BookImportWarningLevel = 'info' | 'warning' | 'error';
export type BookImportExtractMode = 'tail' | 'full';

export interface BookImportWarning {
  code: string;
  message: string;
  level: BookImportWarningLevel;
}

export interface BookImportProjectSuggestion {
  title: string;
  description?: string;
  theme?: string;
  genre?: string;
  narrative_perspective: string;
  target_words: number;
}

export interface BookImportChapter {
  title: string;
  content: string;
  summary?: string;
  chapter_number: number;
  outline_title?: string;
}

export interface BookImportOutline {
  title: string;
  content?: string;
  order_index: number;
  structure?: Record<string, unknown>;
}

export interface BookImportExtractedCharacter {
  name: string;
  role_type: string;
  gender?: string | null;
  age?: string | null;
  personality?: string | null;
  background?: string | null;
  appearance?: string | null;
  current_state?: string | null;
  traits: string[];
  first_seen_chapter?: number | null;
  importance: number;
}

export interface BookImportExtractedRelationship {
  source: string;
  target: string;
  relationship_type: string;
  intimacy_level: number;
  status: string;
  description?: string | null;
}

export interface BookImportExtractedOrganization {
  name: string;
  organization_type?: string | null;
  purpose?: string | null;
  background?: string | null;
  location?: string | null;
  power_level: number;
  members: Array<Record<string, unknown>>;
  traits: string[];
}

export interface BookImportExtractedMemory {
  title: string;
  content: string;
  memory_type: string;
  chapter_number: number;
  related_characters: string[];
  related_locations: string[];
  tags: string[];
  importance_score: number;
}

export interface BookImportExtractedForeshadow {
  title: string;
  content: string;
  plant_chapter_number?: number | null;
  target_resolve_chapter_number?: number | null;
  status: string;
  category?: string | null;
  related_characters: string[];
  tags: string[];
  importance: number;
  strength: number;
  subtlety: number;
}

export interface BookImportAnalysisDossier {
  source_platform_notes: string[];
  world_notes: string[];
  characters: BookImportExtractedCharacter[];
  relationships: BookImportExtractedRelationship[];
  organizations: BookImportExtractedOrganization[];
  memories: BookImportExtractedMemory[];
  foreshadows: BookImportExtractedForeshadow[];
}

export interface BookImportTask {
  task_id: string;
  status: BookImportTaskStatus;
  progress: number;
  message?: string;
  error?: string;
  created_at: string;
  updated_at: string;
}

export interface BookImportPreview {
  task_id: string;
  project_suggestion: BookImportProjectSuggestion;
  chapters: BookImportChapter[];
  outlines: BookImportOutline[];
  warnings: BookImportWarning[];
  analysis_dossier: BookImportAnalysisDossier;
}

export interface BookImportApplyPayload {
  project_suggestion: BookImportProjectSuggestion;
  chapters: BookImportChapter[];
  outlines: BookImportOutline[];
  import_mode?: 'append' | 'overwrite';
}

export interface BookImportCreateTaskPayload {
  file: File;
  extract_mode?: BookImportExtractMode;
  tail_chapter_count?: number;
}

export interface BookImportResult {
  success: boolean;
  project_id: string;
  statistics: {
    chapters: number;
    outlines: number;
    analysis_tasks?: number;
    generated_careers?: number;
    generated_entities?: number;
    generated_world_building?: number;
  };
  warnings: BookImportWarning[];
}

export interface BookImportStepFailure {
  step_name: string;       // world_building / career_system / characters
  step_label: string;      // 中文名
  error: string;           // 错误详情
  retry_count?: number;    // 已重试次数
}

export interface BookImportRetryResult {
  success: boolean;
  project_id: string;
  retry_results: Record<string, number>;
  still_failed: BookImportStepFailure[];
}

export interface BookImportFollowupStatus {
  success: boolean;
  project_id: string;
  status: 'running' | 'needs_action' | 'analysis_running' | 'completed';
  followup_running: boolean;
  followup_state?: {
    status?: string;
    started_at?: string;
    updated_at?: string;
    error?: string | null;
  };
  missing_steps: string[];
  world_completed: boolean;
  counts: {
    chapters: number;
    outlines: number;
    careers: number;
    characters: number;
    organization_characters: number;
    organizations: number;
    relationships: number;
    organization_members: number;
    memories: number;
    foreshadows: number;
  };
  analysis_tasks: {
    total: number;
    completed: number;
    failed: number;
    running: number;
    pending: number;
    by_status: Record<string, number>;
  };
  message?: string;
}

// ==================== 提示词工坊类型定义 ====================

export interface PromptWorkshopItem {
  id: string;
  name: string;
  description?: string;
  prompt_content: string;
  category: string;
  tags?: string[];
  author_name?: string;
  is_official: boolean;
  download_count: number;
  like_count: number;
  is_liked?: boolean;
  created_at?: string;
}

export interface PromptSubmission {
  id: string;
  name: string;
  description?: string;
  prompt_content?: string;
  category: string;
  tags?: string[];
  author_display_name?: string;
  is_anonymous: boolean;
  status: 'pending' | 'approved' | 'rejected';
  review_note?: string;
  reviewed_at?: string;
  created_at?: string;
  source_instance?: string;
  submitter_name?: string;
}

export interface PromptSubmissionCreate {
  name: string;
  description?: string;
  prompt_content: string;
  category: string;
  tags?: string[];
  author_display_name?: string;
  is_anonymous?: boolean;
  source_style_id?: number;
}

export interface PromptWorkshopCategory {
  id: string;
  name: string;
  count: number;
}

export interface PromptWorkshopListResponse {
  success: boolean;
  data: {
    total: number;
    page: number;
    limit: number;
    items: PromptWorkshopItem[];
    categories: PromptWorkshopCategory[];
  };
}

export interface PromptWorkshopStatusResponse {
  mode: 'client' | 'server';
  instance_id: string;
  cloud_url?: string;
  cloud_connected?: boolean;
}

export interface PromptWorkshopAdminStats {
  total_items: number;
  total_official: number;
  total_pending: number;
  total_downloads: number;
  total_likes: number;
}

// 提示词工坊分类常量
export const PROMPT_CATEGORIES: Record<string, string> = {
  general: '通用',
  fantasy: '玄幻/仙侠',
  martial: '武侠',
  romance: '言情',
  scifi: '科幻',
  horror: '悬疑/惊悚',
  history: '历史',
  urban: '都市',
  game: '游戏/电竞',
  other: '其他',
};
