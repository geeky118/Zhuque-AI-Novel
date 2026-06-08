import { useEffect, useState } from 'react';
import { Button, Card, Col, Form, Input, Row, Select, Space, Typography, message, theme } from 'antd';
import { BgColorsOutlined, SaveOutlined } from '@ant-design/icons';
import { projectApi } from '../services/api';
import { useStore } from '../store';
import { COMIC_STYLE_OPTIONS, DEFAULT_COMIC_STYLE, getComicStyleLabel } from '../constants/comicStyles';

const { TextArea } = Input;
const { Text, Title, Paragraph } = Typography;

export default function ComicStyle() {
  const [form] = Form.useForm();
  const { token } = theme.useToken();
  const currentProject = useStore(state => state.currentProject);
  const setCurrentProject = useStore(state => state.setCurrentProject);
  const updateProject = useStore(state => state.updateProject);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!currentProject) return;
    form.setFieldsValue({
      comic_style: currentProject.comic_style || DEFAULT_COMIC_STYLE,
      comic_style_prompt: currentProject.comic_style_prompt || '',
    });
  }, [currentProject, form]);

  const selectedStyle = Form.useWatch('comic_style', form) || currentProject?.comic_style || DEFAULT_COMIC_STYLE;
  const customStylePrompt = Form.useWatch('comic_style_prompt', form);

  const handleSave = async (values: { comic_style: string; comic_style_prompt?: string }) => {
    if (!currentProject) return;
    try {
      setSaving(true);
      const updated = await projectApi.updateProject(currentProject.id, {
        comic_style: values.comic_style || DEFAULT_COMIC_STYLE,
        comic_style_prompt: values.comic_style_prompt?.trim() || null,
      });
      setCurrentProject(updated);
      updateProject(currentProject.id, updated);
      message.success('漫画风格已保存');
    } catch (error) {
      console.error('保存漫画风格失败:', error);
      message.error('漫画风格保存失败，请稍后重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ height: '100%', overflowY: 'auto' }}>
      <div style={{ marginBottom: 20 }}>
        <Title level={3} style={{ marginBottom: 6 }}>
          <BgColorsOutlined style={{ marginRight: 8 }} />
          漫画风格
        </Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          当前项目：{currentProject?.title || ''}
        </Paragraph>
      </div>

      <Form
        form={form}
        layout="vertical"
        onFinish={handleSave}
        initialValues={{
          comic_style: currentProject?.comic_style || DEFAULT_COMIC_STYLE,
          comic_style_prompt: currentProject?.comic_style_prompt || '',
        }}
      >
        <Form.Item
          label="统一漫画风格"
          name="comic_style"
          rules={[{ required: true, message: '请选择漫画风格' }]}
        >
          <Select size="large" options={COMIC_STYLE_OPTIONS.map(option => ({
            value: option.value,
            label: option.label,
          }))} />
        </Form.Item>

        <Row gutter={[12, 12]} style={{ marginBottom: 20 }}>
          {COMIC_STYLE_OPTIONS.map(option => {
            const active = selectedStyle === option.value;
            return (
              <Col xs={24} sm={12} lg={8} key={option.value}>
                <Card
                  hoverable
                  size="small"
                  onClick={() => form.setFieldValue('comic_style', option.value)}
                  style={{
                    height: '100%',
                    borderColor: active ? token.colorPrimary : token.colorBorderSecondary,
                    borderWidth: active ? 2 : 1,
                    background: active ? token.colorFillQuaternary : token.colorBgContainer,
                  }}
                >
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    <Text strong>{option.label}</Text>
                    <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.6 }}>
                      {option.description}
                    </Text>
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>

        <Form.Item
          label="自定义风格补充"
          name="comic_style_prompt"
        >
          <TextArea
            rows={6}
            maxLength={800}
            showCount
            placeholder="可选：补充固定的线条、上色、光影、镜头、人物比例、服装细节等要求"
          />
        </Form.Item>

        <Card size="small" style={{ marginBottom: 20 }}>
          <Space direction="vertical" size={6}>
            <Text type="secondary">当前生效风格</Text>
            <Text strong>{getComicStyleLabel(selectedStyle)}</Text>
            {customStylePrompt && (
              <Text style={{ whiteSpace: 'pre-wrap' }}>{customStylePrompt}</Text>
            )}
          </Space>
        </Card>

        <Form.Item>
          <Button
            type="primary"
            htmlType="submit"
            icon={<SaveOutlined />}
            size="large"
            loading={saving}
            disabled={!currentProject}
          >
            保存漫画风格
          </Button>
        </Form.Item>
      </Form>
    </div>
  );
}
