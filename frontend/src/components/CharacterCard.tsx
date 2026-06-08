import { Button, Card, Space, Tag, Typography, Popconfirm, theme } from 'antd';
import { EditOutlined, DeleteOutlined, UserOutlined, BankOutlined, ExportOutlined, PictureOutlined } from '@ant-design/icons';
import { characterCardStyles } from './CardStyles';
import type { Character, CharacterImageState } from '../types';
import { buildAppPath } from '../utils/basePath';

const { Text, Paragraph } = Typography;

interface CharacterCardProps {
  character: Character;
  imageState?: CharacterImageState;
  onEdit?: (character: Character) => void;
  onDelete: (id: string) => void;
  onExport?: () => void;
  onManageImage?: (character: Character) => void;
}

export const CharacterCard: React.FC<CharacterCardProps> = ({
  character,
  imageState,
  onEdit,
  onDelete,
  onExport,
  onManageImage,
}) => {
  const { token } = theme.useToken();

  const getRoleTypeColor = (roleType?: string) => {
    const roleColors: Record<string, string> = {
      'protagonist': 'blue',
      'supporting': 'green',
      'antagonist': 'red',
    };
    return roleColors[roleType || ''] || 'default';
  };

  const getRoleTypeLabel = (roleType?: string) => {
    const roleLabels: Record<string, string> = {
      'protagonist': '主角',
      'supporting': '配角',
      'antagonist': '反派',
    };
    return roleLabels[roleType || ''] || '其他';
  };

  const isOrganization = character.is_organization;
  const charStatus = character.status || 'active';
  const isInactive = charStatus !== 'active';
  const imageSrc = imageState?.image_url ? buildAppPath(imageState.image_url) : undefined;
  const variantCount = imageState?.variant_count ?? 1;

  const getVariantTypeLabel = (variantType?: string) => {
    const variantTypeLabels: Record<string, string> = {
      default: '默认',
      period: '时期/阶段',
      volume: '分卷',
    };
    return variantTypeLabels[variantType || ''] || '版本';
  };

  const getVariantRangeLabel = () => {
    if (!imageState) return null;
    if (imageState.chapter_start == null && imageState.chapter_end == null) {
      return null;
    }
    if (imageState.chapter_start != null && imageState.chapter_end != null) {
      return `第 ${imageState.chapter_start}-${imageState.chapter_end} 章`;
    }
    if (imageState.chapter_start != null) {
      return `第 ${imageState.chapter_start} 章起`;
    }
    return `至第 ${imageState.chapter_end} 章`;
  };

  const getStatusTag = () => {
    const statusConfig: Record<string, { color: string; label: string }> = {
      deceased: { color: token.colorTextBase, label: '💀 已死亡' },
      missing: { color: token.colorWarning, label: '❓ 已失踪' },
      retired: { color: token.colorTextTertiary, label: '📤 已退场' },
      destroyed: { color: token.colorTextBase, label: '💀 已覆灭' },
    };
    const config = statusConfig[charStatus];
    if (!config) return null;
    return <Tag color={config.color} style={{ marginLeft: 4 }}>{config.label}</Tag>;
  };

  const getImageStatusTag = () => {
    if (!imageState) return null;

    const statusMap: Record<string, { color: string; label: string }> = {
      generating: { color: 'processing', label: '生成中' },
      ready: { color: 'success', label: '已生成' },
      capacity: { color: 'warning', label: '接口繁忙' },
      policy: { color: 'orange', label: '提示词需调整' },
      failed: { color: 'error', label: '生成失败' },
    };
    const config = statusMap[imageState.status];
    if (!config) return null;
    return <Tag color={config.color}>{config.label}</Tag>;
  };

  return (
    <Card
      hoverable
      style={{
        ...(isOrganization ? characterCardStyles.organizationCard : characterCardStyles.characterCard),
        ...(isInactive ? { opacity: 0.6, filter: 'grayscale(40%)' } : {}),
      }}
      styles={{
        body: {
          flex: 1,
          overflow: 'auto',
          display: 'flex',
          flexDirection: 'column'
        },
        actions: {
          borderRadius: '0 0 12px 12px'
        }
      }}
      actions={[
        ...(onManageImage ? [<PictureOutlined key="image" onClick={() => onManageImage(character)} />] : []),
        ...(onEdit ? [<EditOutlined key="edit" onClick={() => onEdit(character)} />] : []),
        ...(onExport ? [<ExportOutlined key="export" onClick={onExport} />] : []),
        <Popconfirm
          key="delete"
          title={`确定删除这个${isOrganization ? '组织' : '角色'}吗？`}
          onConfirm={() => onDelete(character.id)}
          okText="确定"
          cancelText="取消"
        >
          <DeleteOutlined />
        </Popconfirm>,
      ]}
    >
      <Card.Meta
        avatar={
          isOrganization ? (
            <BankOutlined style={{ fontSize: 32, color: token.colorSuccess }} />
          ) : (
            <UserOutlined style={{ fontSize: 32, color: token.colorPrimary }} />
          )
        }
        title={
          <Space>
            <span style={characterCardStyles.nameEllipsis}>{character.name}</span>
            {isOrganization ? (
              <Tag color="green">组织</Tag>
            ) : (
              character.role_type && (
                <Tag color={getRoleTypeColor(character.role_type)}>
                  {getRoleTypeLabel(character.role_type)}
                </Tag>
              )
            )}
            {getStatusTag()}
          </Space>
        }
        description={
          <div style={characterCardStyles.descriptionBlock}>
            <div
              style={{
                marginBottom: 12,
                borderRadius: 12,
                overflow: 'hidden',
                border: `1px solid ${token.colorBorderSecondary}`,
                background: isOrganization
                  ? 'linear-gradient(135deg, rgba(22, 163, 74, 0.12), rgba(15, 118, 110, 0.08))'
                  : 'linear-gradient(135deg, rgba(37, 99, 235, 0.12), rgba(217, 119, 6, 0.08))',
              }}
            >
              {imageSrc ? (
                <img
                  key={imageSrc}
                  src={imageSrc}
                  alt={`${character.name}形象图`}
                  style={{
                    display: 'block',
                    width: '100%',
                    aspectRatio: '1 / 1',
                    objectFit: 'cover',
                  }}
                />
              ) : (
                <div
                  style={{
                    aspectRatio: '1 / 1',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 8,
                    color: token.colorTextTertiary,
                  }}
                >
                  {isOrganization ? (
                    <BankOutlined style={{ fontSize: 28 }} />
                  ) : (
                    <PictureOutlined style={{ fontSize: 28 }} />
                  )}
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {imageState?.status === 'generating'
                      ? '形象图生成中'
                      : isOrganization
                        ? '暂无组织概念图'
                        : '暂无角色形象图'}
                  </Text>
                </div>
              )}
            </div>

            {(imageState || onManageImage) && (
              <div style={{ marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <Text type="secondary" style={{ flexShrink: 0 }}>AI形象：</Text>
                  <Space size={[4, 4]} wrap style={{ justifyContent: 'flex-end' }}>
                    {getImageStatusTag()}
                    {imageState?.has_image && <Tag color="blue">可预览</Tag>}
                  </Space>
                </div>
                <Space size={[4, 4]} wrap style={{ marginTop: 8 }}>
                  {imageState && (
                    <>
                      <Tag>{imageState.variant_label}</Tag>
                      <Tag color="purple">{getVariantTypeLabel(imageState.variant_type)}</Tag>
                      {variantCount > 1 && <Tag color="gold">{variantCount} 个版本</Tag>}
                      {getVariantRangeLabel() && <Tag color="cyan">{getVariantRangeLabel()}</Tag>}
                    </>
                  )}
                </Space>
                {onManageImage && (
                  <Button
                    type="link"
                    size="small"
                    style={{ paddingInline: 0, marginTop: 6 }}
                    onClick={() => onManageImage(character)}
                  >
                    管理形象版本
                  </Button>
                )}
              </div>
            )}

            {/* 角色特有字段 */}
            {!isOrganization && (
              <>
                {character.age && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>年龄：</Text>
                    <Text style={{ flex: 1 }}>{character.age}</Text>
                  </div>
                )}
                {character.gender && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>性别：</Text>
                    <Text style={{ flex: 1 }}>{character.gender}</Text>
                  </div>
                )}
                {character.personality && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>性格：</Text>
                    <Text
                      style={{ flex: 1, minWidth: 0 }}
                      ellipsis={{ tooltip: character.personality }}
                    >
                      {character.personality}
                    </Text>
                  </div>
                )}
                {character.relationships && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>关系：</Text>
                    <Text
                      style={{ flex: 1, minWidth: 0 }}
                      ellipsis={{ tooltip: character.relationships }}
                    >
                      {character.relationships}
                    </Text>
                  </div>
                )}
              </>
            )}

            {/* 组织特有字段 */}
            {isOrganization && (
              <>
                {character.organization_type && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>类型：</Text>
                    <Tag color="cyan">{character.organization_type}</Tag>
                  </div>
                )}
                {character.power_level !== undefined && character.power_level !== null && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>势力等级：</Text>
                    <Tag color={character.power_level >= 70 ? 'red' : character.power_level >= 50 ? 'orange' : 'default'}>
                      {character.power_level}
                    </Tag>
                  </div>
                )}
                {character.location && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>所在地：</Text>
                    <Text
                      style={{ flex: 1, minWidth: 0 }}
                      ellipsis={{ tooltip: character.location }}
                    >
                      {character.location}
                    </Text>
                  </div>
                )}
                {character.color && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>代表颜色：</Text>
                    <Text style={{ flex: 1, minWidth: 0 }}>{character.color}</Text>
                  </div>
                )}
                {character.motto && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>格言：</Text>
                    <Text
                      style={{ flex: 1, minWidth: 0 }}
                      ellipsis={{ tooltip: character.motto }}
                    >
                      {character.motto}
                    </Text>
                  </div>
                )}
                {character.organization_purpose && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>目的：</Text>
                    <Text
                      style={{ flex: 1, minWidth: 0 }}
                      ellipsis={{ tooltip: character.organization_purpose }}
                    >
                      {character.organization_purpose}
                    </Text>
                  </div>
                )}
                {character.organization_members && (
                  <div style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                    <Text type="secondary" style={{ flexShrink: 0 }}>成员：</Text>
                    <Text style={{ flex: 1, minWidth: 0, fontSize: 12, lineHeight: 1.6, wordBreak: 'break-all' }}>
                      {typeof character.organization_members === 'string'
                        ? character.organization_members
                        : JSON.stringify(character.organization_members)}
                    </Text>
                  </div>
                )}
              </>
            )}

            {/* 通用字段 - 背景信息截断显示 */}
            {character.background && (
              <div style={{ marginTop: 12 }}>
                <Paragraph
                  type="secondary"
                  style={{ fontSize: 12, marginBottom: 0 }}
                  ellipsis={{ tooltip: character.background, rows: 3 }}
                >
                  {character.background}
                </Paragraph>
              </div>
            )}
          </div>
        }
      />
    </Card>
  );
};
