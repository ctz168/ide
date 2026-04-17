# 🚀 PhoneIDE Android 构建触发指南

## 📋 概述

多选功能已经成功集成到PhoneIDE项目中，现在可以触发Android APK构建，将包含多选功能的APK打包发布。

## ✅ 当前状态

- ✅ **代码已推送**: 多选功能代码已推送到 `ctz168/ide` 仓库
- ✅ **工作流就绪**: Android构建工作流已配置好，会自动拉取最新代码
- ✅ **功能完整**: 所有多选功能已集成并测试

## 📱 手动触发构建

由于GitHub CLI不可用，需要手动触发构建：

### 步骤1: 访问GitHub Actions
1. 打开浏览器访问: https://github.com/ctz168/phoneide/actions
2. 确保您在 `ctz168/phoneide` 仓库

### 步骤2: 选择工作流
1. 在左侧菜单中找到 "Build & Release APK" 工作流
2. 点击该工作流

### 步骤3: 运行工作流
1. 点击右侧的 "Run workflow" 按钮
2. 填写参数：
   - **ide_ref**: `main` (默认值，使用main分支的最新代码)
   - **build_type**: `both` (构建debug和release版本)

### 步骤4: 开始构建
1. 点击 "Run workflow" 按钮
2. 构建将自动开始，预计需要5-10分钟

## 🔍 构建过程

工作流会自动执行以下步骤：

1. **拉取IDE代码**: 从 `ctz168/ide` 克隆最新代码（包含多选功能）
2. **复制资源**: 将IDE文件复制到Android项目的assets目录
3. **构建APK**: 使用Android SDK构建debug和release版本
4. **签名**: 自动为release版本签名
5. **发布**: 将APK上传到GitHub Releases

## 📦 构建产物

构建完成后，您将获得：

- **PhoneIDE-debug.apk** - 调试版本，可直接安装测试
- **PhoneIDE-release.apk** - 发布版本，可分发给用户
- **版本信息**: 包含IDE代码commit hash，便于追踪多选功能的版本

## 🎯 多选功能确认

构建的APK将包含以下多选功能：

### 核心功能
- ✅ 多光标编辑 (`Alt + 点击`)
- ✅ 词汇选择 (`Alt + A`)
- ✅ 垂直光标添加 (`Shift + Alt + ↑/↓`)
- ✅ 移动端支持 (`Ctrl + 点击`)
- ✅ 块选择支持

### 文件包含
- `static/js/editor.js` - 多选功能核心实现
- `static/css/style.css` - 多选样式
- `static/demo.html` - 功能演示页面
- `MULTI_SELECT_README.md` - 使用说明

## ⏱️ 时间预估

- **构建时间**: 5-10分钟
- **APK大小**: ~15-20MB
- **首次运行**: 需要额外下载Ubuntu rootfs (~300MB)
- **总需求**: ~500MB可用空间

## 🔗 相关链接

- **PhoneIDE主仓库**: https://github.com/ctz168/phoneide
- **IDE服务仓库**: https://github.com/ctz168/ide
- **构建状态**: https://github.com/ctz168/phoneide/actions
- **发布版本**: https://github.com/ctz168/phoneide/releases

## 🐛 故障排除

### 构建失败
1. 检查GitHub Actions页面查看具体错误信息
2. 确保代码已正确推送到 `ctz168/ide`
3. 检查工作流参数是否正确

### 功能测试
1. 安装APK后，打开编辑器
2. 尝试使用 `Alt + 点击` 添加多个光标
3. 测试 `Alt + A` 选择所有相同词汇
4. 验证移动端 `Ctrl + 点击` 功能

## 📝 注意事

1. **网络要求**: 构建需要稳定的网络连接
2. **权限要求**: 需要GitHub仓库的读写权限
3. **存储空间**: 构建过程需要足够的磁盘空间
4. **依赖项**: 自动安装所有必需的依赖项

---

## 🎉 下一步

构建完成后，您可以：
1. 下载并测试APK
2. 验证多选功能是否正常工作
3. 根据需要进一步优化功能
4. 将APK分发给用户使用

**注意**: 构建是自动化的，每次推送到 `ctz168/ide` 的main分支都会自动触发新的构建！