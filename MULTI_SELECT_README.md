# 🎯 轻量级多选功能实现

## 概述

为 PhoneIDE 的 CodeMirror 5 编辑器添加了轻量级的多选功能，支持多光标编辑和块选择。

## 🚀 功能特性

### 核心功能
- ✅ **多光标编辑** - 在多个位置同时创建光标
- ✅ **块选择** - 支持矩形选择区域
- ✅ **词汇选择** - 一键选择所有相同词汇
- ✅ **移动端支持** - 自适应触摸操作
- ✅ **键盘快捷键** - 便捷的操作方式

### 支持的操作
- `Alt + 点击` - 添加新光标（桌面端）
- `Ctrl + 点击` - 添加新光标（移动端）
- `Alt + A` - 选择所有相同词汇
- `Shift + Alt + ↑/↓` - 在上方/下方添加光标
- `Esc` - 退出多选模式

## 📁 文件修改

### 1. 编辑器核心 (`js/editor.js`)

#### 新增状态变量
```javascript
// 多选状态
let multiSelectMode = false;     // 是否激活多选模式
let multiCursors = [];            // 光标位置数组
let selectionRanges = [];        // 选择区域数组
```

#### 新增配置
```javascript
const config = {
    // ... 原有配置 ...
    multiSelect: {
        enabled: true,
        modifierKey: 'Alt',           // 修饰键
        rectangular: true,            // 启用矩形选择
        maxCursors: 50                // 最大光标数
    }
};
```

#### 新增快捷键
```javascript
extraKeys: {
    // ... 原有快捷键 ...
    'Alt-Click': (cm, event) => {
        if (config.multiSelect.enabled) {
            event.preventDefault();
            handleMultiSelectClick(event);
        }
    },
    'Alt-A': (cm) => {
        if (config.multiSelect.enabled) {
            selectAllOccurrences();
        }
    },
    'Escape': (cm) => {
        if (multiSelectMode) {
            exitMultiSelect();
        }
    }
}
```

#### 核心函数
- `handleMultiSelectClick()` - 处理多选点击
- `enterMultiSelect()` - 进入多选模式
- `exitMultiSelect()` - 退出多选模式
- `addCursorAt()` - 在指定位置添加光标
- `selectAllOccurrences()` - 选择所有匹配项
- `updateMultiSelectDisplay()` - 更新显示

### 2. 样式文件 (`css/style.css`)

新增多选相关样式：
```css
.CodeMirror-multi-select-cursor {
    border-left: 2px solid #ff6b6b !important;
    opacity: 0.7 !important;
}

.CodeMirror-multi-select-active {
    border-left: 2px solid var(--accent) !important;
    opacity: 1.0 !important;
}

.multi-select-notification {
    animation: slideIn 0.3s ease-out;
}
```

### 3. 演示页面 (`demo.html`)

完整的多选功能演示页面，包含：
- 交互式编辑器
- 操作说明
- 实时状态显示
- 示例代码

## 🎮 使用方法

### 基本操作
1. **添加光标** - 按住 `Alt` 键点击要添加光标的位置
2. **选择词汇** - 选中一个词汇后按 `Alt + A` 选择所有匹配项
3. **退出多选** - 按 `Esc` 键退出多选模式

### 高级操作
1. **垂直添加光标** - 使用 `Shift + Alt + ↑/↓` 在垂直方向添加光标
2. **矩形选择** - 按住 `Alt` 键拖拽进行矩形选择
3. **批量编辑** - 在多选模式下输入文本会同时在所有光标位置插入

### 移动端适配
- 使用 `Ctrl + 点击` 替代 `Alt + 点击`
- 自动检测设备类型并调整快捷键

## 🔧 技术实现

### 轻量级设计
- **无依赖** - 不需要额外的库或插件
- **向后兼容** - 完全兼容现有的 CodeMirror 5 功能
- **性能优化** - 限制最大光标数量避免性能问题

### 模拟实现
由于 CodeMirror 5 不支持真正的多光标，采用以下策略：
1. **状态管理** - 维护光标位置数组
2. **视觉反馈** - 通过状态栏显示光标数量
3. **批量操作** - 在编辑操作时应用到所有光标位置

### 限制说明
- CodeMirror 5 的限制：无法同时显示多个光标
- 通过状态栏和操作反馈来弥补视觉体验
- 建议未来升级到 CodeMirror 6 以获得完整的多光标支持

## 📱 兼容性

- ✅ CodeMirror 5.x
- ✅ 桌面端浏览器
- ✅ 移动端浏览器
- ✅ 触摸操作
- ✅ 键盘操作

## 🚀 部署

1. 确保文件路径正确
2. 重启 IDE 服务器
3. 访问 `/demo.html` 查看演示

## 🎯 下一步优化

1. **真正的多光标显示** - 考虑升级到 CodeMirror 6
2. **更多选择模式** - 支持正则表达式选择
3. **性能优化** - 大文件时的性能优化
4. **UI 增强** - 更好的视觉反馈

---

## 📞 支持

如有问题或建议，请查看：
- 演示页面：`/demo.html`
- 核心实现：`js/editor.js`
- 样式定义：`css/style.css`