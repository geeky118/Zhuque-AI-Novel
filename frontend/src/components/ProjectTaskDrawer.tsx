import { useCallback, useEffect, useMemo, useState } from 'react';
import { Badge, Button, Drawer, Empty, Progress, Space, Spin, Tag, Tooltip, Typography, theme } from 'antd';
import { ClockCircleOutlined, SyncOutlined } from '@ant-design/icons';
import { projectTaskApi } from '../services/api';
import type { ProjectBackgroundTask, ProjectTaskSummaryResponse } from '../types';

const { Text } = Typography;

interface ProjectTaskDrawerProps {
  projectId: string;
  alphaColor: (color: string, alpha: number) => string;
}

const ACTIVE_STATUSES = new Set(['pending', 'queued', 'running', 'generating', 'processing']);

const statusLabel: Record<string, string> = {
  pending: '排队中',
  queued: '排队中',
  running: '进行中',
  generating: '生成中',
  processing: '处理中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  partial_failed: '部分失败',
};

const statusColor = (status: string) => {
  if (ACTIVE_STATUSES.has(status)) return 'processing';
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'error';
  if (status === 'partial_failed') return 'warning';
  if (status === 'cancelled') return 'default';
  return 'default';
};

const formatElapsed = (seconds?: number | null) => {
  if (seconds === null || seconds === undefined) return '-';
  const safe = Math.max(0, seconds);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  if (hours > 0) return `${hours}小时${minutes}分`;
  if (minutes > 0) return `${minutes}分${secs}秒`;
  return `${secs}秒`;
};

const formatTime = (value?: string | null) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleString();
};

const taskTypeLabel: Record<string, string> = {
  chapter_batch: '章节',
  outline_batch_expand: '大纲',
  storyboard: '分镜',
  storyboard_batch: '分镜',
  comic_page_regenerate: '漫画',
  comic_page_edit: '改图',
  comic_batch: '漫画',
  full_pipeline: '全流程',
  visual_bible_batch: '角色',
};

function TaskItem({ task }: { task: ProjectBackgroundTask }) {
  const { token } = theme.useToken();
  const progressStatus = task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : 'active';
  const errors = Array.isArray(task.errors) ? task.errors.filter(Boolean) : [];

  return (
    <div
      style={{
        padding: '14px 0',
        borderBottom: `1px solid ${token.colorBorderSecondary}`,
      }}
    >
      <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <Space size={8} wrap>
            <Text strong>{task.title}</Text>
            <Tag color={statusColor(task.status)}>{statusLabel[task.status] || task.status}</Tag>
            <Tag>{taskTypeLabel[task.type] || task.type}</Tag>
          </Space>
          {task.current && (
            <div style={{ marginTop: 6 }}>
              <Text type="secondary">{task.current}</Text>
            </div>
          )}
        </div>
        <Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
          <ClockCircleOutlined /> {formatElapsed(task.elapsed_seconds)}
        </Text>
      </Space>

      <div style={{ marginTop: 10 }}>
        <Progress
          percent={Math.max(0, Math.min(100, task.progress || 0))}
          status={progressStatus}
          size="small"
        />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginTop: 10 }}>
        <Text type="secondary">进度：{task.completed ?? 0}/{task.total ?? '-'}</Text>
        <Text type="secondary">更新：{formatTime(task.updated_at || task.created_at)}</Text>
      </div>

      {task.message && (
        <div style={{ marginTop: 8 }}>
          <Text>{task.message}</Text>
        </div>
      )}
      {task.error_message && (
        <div style={{ marginTop: 8 }}>
          <Text type="danger">{task.error_message}</Text>
        </div>
      )}
      {errors.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <Text type="secondary">错误记录：{errors.length} 条</Text>
        </div>
      )}
    </div>
  );
}

export default function ProjectTaskDrawer({ projectId, alphaColor }: ProjectTaskDrawerProps) {
  const { token } = theme.useToken();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<ProjectTaskSummaryResponse | null>(null);

  const loadTasks = useCallback(async (options?: { silent?: boolean }) => {
    try {
      setLoading(true);
      const response = await projectTaskApi.getProjectTasks(projectId, { silent: options?.silent ?? true });
      setSummary(response);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void loadTasks({ silent: true });
  }, [loadTasks]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadTasks({ silent: true });
    }, summary?.running_count ? 3000 : 10000);
    return () => window.clearInterval(timer);
  }, [loadTasks, summary?.running_count]);

  const runningCount = summary?.running_count || 0;
  const tasks = useMemo(() => summary?.tasks || [], [summary?.tasks]);

  return (
    <>
      <Tooltip title="后台任务">
        <Badge count={runningCount} size="small" offset={[-4, 4]}>
          <Button
            icon={<SyncOutlined spin={runningCount > 0} />}
            onClick={() => {
              setOpen(true);
              void loadTasks({ silent: true });
            }}
            style={{
              height: 56,
              borderRadius: 14,
              color: token.colorWhite,
              border: `1px solid ${alphaColor(token.colorWhite, 0.14)}`,
              background: alphaColor(token.colorWhite, 0.1),
              boxShadow: `inset 0 1px 0 ${alphaColor(token.colorWhite, 0.16)}, 0 4px 10px ${alphaColor(token.colorText, 0.1)}`,
            }}
          >
            任务（{runningCount}）
          </Button>
        </Badge>
      </Tooltip>

      <Drawer
        title={
          <Space>
            <span>后台任务</span>
            <Tag color={runningCount > 0 ? 'processing' : 'default'}>进行中 {runningCount}</Tag>
          </Space>
        }
        placement="right"
        width={520}
        open={open}
        onClose={() => setOpen(false)}
        extra={
          <Button icon={<SyncOutlined />} onClick={() => void loadTasks({ silent: true })} loading={loading}>
            刷新
          </Button>
        }
      >
        {loading && !summary ? (
          <div style={{ padding: 48, textAlign: 'center' }}>
            <Spin />
          </div>
        ) : tasks.length === 0 ? (
          <Empty description="暂无后台任务" />
        ) : (
          <div>
            {tasks.map((task) => (
              <TaskItem key={`${task.type}:${task.task_id}`} task={task} />
            ))}
          </div>
        )}
      </Drawer>
    </>
  );
}
