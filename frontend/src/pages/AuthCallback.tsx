import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Spin, Result, Button, Modal, Input, message, theme } from 'antd';
import { authApi } from '../services/api';

export default function AuthCallback() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<'loading' | 'success' | 'error'>('loading');
  const [errorMessage, setErrorMessage] = useState('');
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  const { token } = theme.useToken();
  const alphaColor = (color: string, alpha: number) => `color-mix(in srgb, ${color} ${(alpha * 100).toFixed(0)}%, transparent)`;
  interface PasswordStatus {
    has_password: boolean;
    has_custom_password: boolean;
    username: string;
    default_password: string;
  }
  const [passwordStatus, setPasswordStatus] = useState<PasswordStatus | null>(null);
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [settingPassword, setSettingPassword] = useState(false);

  const navigateAfterLogin = (delay = 500) => {
    const redirect = sessionStorage.getItem('login_redirect') || '/';
    sessionStorage.removeItem('login_redirect');
    setTimeout(() => navigate(redirect), delay);
  };

  useEffect(() => {
    const handleCallback = async () => {
      try {
        const currentUser = await authApi.getCurrentUser();
        const isFirstLogin = document.cookie.includes('first_login=true');
        setStatus('success');

        if (isFirstLogin) {
          const defaultPassword = `${currentUser.username}@666`;
          setPasswordStatus({
            has_password: false,
            has_custom_password: false,
            username: currentUser.username,
            default_password: defaultPassword,
          });
          document.cookie = 'first_login=; path=/; max-age=0';
          setTimeout(() => setShowPasswordModal(true), 1000);
          return;
        }

        navigateAfterLogin(1000);
      } catch (error) {
        console.error('登录失败:', error);
        setStatus('error');
        setErrorMessage('登录失败，请重试');
      }
    };

    handleCallback();
  }, [navigate]);

  if (status === 'loading') {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        background: `linear-gradient(135deg, ${token.colorPrimary} 0%, ${token.colorPrimaryHover} 100%)`,
      }}>
        <div style={{ textAlign: 'center' }}>
          <Spin size="large" />
          <div style={{ marginTop: 20, color: token.colorWhite, fontSize: 16 }}>
            正在处理登录...
          </div>
        </div>
      </div>
    );
  }

  if (status === 'error') {
    return (
      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        background: `linear-gradient(135deg, ${token.colorPrimary} 0%, ${token.colorPrimaryHover} 100%)`,
      }}>
        <Result
          status="error"
          title="登录失败"
          subTitle={errorMessage}
          extra={
            <Button type="primary" onClick={() => navigate('/login')}>
              返回登录
            </Button>
          }
          style={{ background: token.colorBgContainer, padding: 40, borderRadius: 8 }}
        />
      </div>
    );
  }

  const handleSetPassword = async () => {
    const passwordToSet = newPassword || passwordStatus?.default_password;

    if (!passwordToSet) {
      message.error('请输入新密码');
      return;
    }
    if (passwordToSet.length < 6) {
      message.error('密码长度至少为6个字符');
      return;
    }
    if (newPassword && newPassword !== confirmPassword) {
      message.error('两次输入的密码不一致');
      return;
    }

    setSettingPassword(true);
    try {
      const isFirstLogin = !passwordStatus?.has_password;
      if (isFirstLogin) {
        await authApi.initializePassword(passwordToSet);
        message.success('密码初始化成功');
      } else {
        await authApi.setPassword(passwordToSet);
        message.success('密码设置成功');
      }
      setShowPasswordModal(false);
      navigateAfterLogin();
    } catch {
      message.error('密码设置失败，请重试');
    } finally {
      setSettingPassword(false);
    }
  };

  const handleSkipPasswordSetting = async () => {
    const isFirstLogin = !passwordStatus?.has_password;
    if (isFirstLogin && passwordStatus?.default_password) {
      try {
        await authApi.initializePassword(passwordStatus.default_password);
      } catch (error) {
        console.error('初始化默认密码失败:', error);
      }
    }

    setShowPasswordModal(false);
    navigateAfterLogin();
  };

  return (
    <>
      <Modal
        title="设置账号密码"
        open={showPasswordModal}
        centered
        onOk={handleSetPassword}
        onCancel={handleSkipPasswordSetting}
        confirmLoading={settingPassword}
        okText="设置密码"
        cancelText="暂不设置"
        width={500}
      >
        <div style={{ marginBottom: 20 }}>
          <p>您已成功登录。</p>
          <p>系统已为您自动生成默认密码，您可以选择设置自定义密码或继续使用默认密码。</p>
          {passwordStatus?.default_password && (
            <div style={{
              background: token.colorFillTertiary,
              padding: 12,
              borderRadius: 4,
              marginTop: 12,
            }}>
              <strong>账号：</strong>{passwordStatus.username}<br />
              <strong>默认密码：</strong><code style={{
                background: token.colorBgContainer,
                padding: '2px 8px',
                borderRadius: 3,
                color: token.colorPrimary,
                fontSize: 14,
              }}>{passwordStatus.default_password}</code>
            </div>
          )}
        </div>

        <div style={{ marginTop: 20 }}>
          <div style={{ marginBottom: 12 }}>
            <label>新密码（至少6个字符）：</label>
            <Input.Password
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="请输入新密码"
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <label>确认密码：</label>
            <Input.Password
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="请再次输入密码"
              style={{ marginTop: 4 }}
            />
          </div>
        </div>
      </Modal>

      <div style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        minHeight: '100vh',
        background: `linear-gradient(135deg, ${token.colorPrimary} 0%, ${token.colorPrimaryHover} 100%)`,
      }}>
        <Result
          status="success"
          title="登录成功"
          subTitle={showPasswordModal ? '请设置账号密码...' : '正在跳转...'}
          style={{ background: alphaColor(token.colorBgContainer, 0.96), padding: 40, borderRadius: 8 }}
        />
      </div>
    </>
  );
}
