// Read identity and capabilities using the same locator as the running adapter.
(async () => {
    const require = process.mainModule.require.bind(process.mainModule);
    const electron = require('electron');
    const compat = __MIKO_COMPAT__;
    const base = {pid: process.pid, executable: process.execPath, version: electron.app.getVersion(),
        account: compat.account(globalThis.authData), nickname: globalThis.authData?.nickName || ''};
    if (!base.account) return {...base, state: 'login', stage: 'login', note: '请先在此 QQ 客户端完成登录'};
    try {
        const view = await compat.locate(electron);
        return {...base, state: 'ready', stage: 'ready', capabilities: view.capabilities,
                note: view.capabilities.send_images ? '已登录，可连接' : '可连接文字聊天；当前客户端的图片发送接口暂不可用'};
    } catch (error) {
        const reasons = {
            MIKO_UI_NOT_READY: ['loading', 'window', 'QQ 主界面尚未就绪，请打开主界面后重新检测'],
            MIKO_UI_AMBIGUOUS: ['loading', 'window', '检测到多个可用 QQ 窗口，请关闭独立聊天或设置窗口后重新检测'],
            MIKO_IPC_UNAVAILABLE: ['unsupported', 'ipc', '未找到此 QQ 版本的消息通信接口'],
            MIKO_FRIENDS_UNAVAILABLE: ['unsupported', 'friends', '已识别 QQ 界面，但未找到此版本的好友服务']
        };
        const [state, stage, note] = reasons[error.message] || ['unsupported', 'services', '无法识别此 QQ 版本的消息服务'];
        return {...base, state, stage, note};
    }
})();
