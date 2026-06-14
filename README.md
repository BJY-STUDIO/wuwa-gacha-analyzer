# wuwa本地简易抽卡分析工具

鸣潮（Wuthering Waves）抽卡记录本地分析工具。纯本地运行，数据不上传到任何第三方服务器。

## 功能概览

- 从游戏 API 抓取全部 11 种卡池的抽卡记录
- 自动计算 5 星/4 星保底进度、UP 状态、大小保底判定
- 宫格 + 横向两种记录展示模式
- 合并历史数据，支持增量更新
- 5 星出货概率分布可视化
- 深色/浅色主题切换（默认跟随 Windows 系统主题）
- 角色/武器图标本地缓存，减少 CDN 请求
- 纯前端渲染，数据始终留在本地

## 快速开始

### 环境要求

- Python 3.8+
- 依赖：`requests`、`flask`

### 安装依赖

```bash
pip install requests flask
```

### 使用流程

**第 1 步：抓取抽卡记录**

```bash
python wuwa_gacha.py
```

运行后按提示粘贴游戏内唤取记录的 JSON 凭证（获取方式见下方），脚本会自动抓取全部卡池记录并保存为 JSON 文件。

也可通过命令行直接传入凭证：

```bash
# 直接传入 JSON 字符串
python wuwa_gacha.py '{"recordId":"xxx","playerId":"xxx","serverId":"xxx","cardPoolId":"xxx",...}'

# 或从文件读取
python wuwa_gacha.py -f credentials.json
```

**第 2 步：启动 Web 服务查看分析**

```bash
python wuwa_server.py
```

浏览器访问 http://localhost:8766 ，上传第 1 步生成的 JSON 文件即可查看分析结果。

可选参数：

```bash
python wuwa_server.py --port 9000  # 指定端口，默认 8766
```

### 其他方式：生成静态 HTML 报告

如果不想要 Web 服务，也可以直接生成一个独立的 HTML 文件：

```bash
python gacha_report.py              # 自动查找最新 JSON
python gacha_report.py data.json    # 指定 JSON 文件
```

会在同目录生成 `gacha_report.html`，双击即可在浏览器中打开。

## 脚本功能说明

| 脚本 | 功能 | 是否必需 |
|------|------|---------|
| `wuwa_gacha.py` | 从游戏 API 抓取抽卡记录，保存为 JSON | 是（获取数据） |
| `wuwa_server.py` | 本地 Web 服务，上传/合并/分析/下载 | 主力工具 |
| `gacha_report.py` | 生成静态 HTML 报告 | 可选替代方案 |

**推荐用法**：先用 `wuwa_gacha.py` 拉取数据，再用 `wuwa_server.py` 做日常分析和数据管理。

## 如何获取抽卡凭证

1. 打开鸣潮游戏，进入唤取记录页面
2. 使用抓包工具（如 Fiddler、Charles）捕获 `gmserver-api.aki-game2.com/gacha/record/query` 请求
3. 从请求体中提取以下字段组成 JSON：

```json
{
  "recordId": "xxx",
  "playerId": "xxx",
  "serverId": "xxx",
  "cardPoolId": "xxx",
  "cardPoolType": 1,
  "languageCode": "zh-Hans"
}
```

也可使用社区提供的抓包工具（如 SniffDroid、HttpCanary）在手机端获取。

## Fluent UI 2 设计

本项目的 Web 界面基于 Microsoft Fluent UI 2 设计规范实现，包括：

- **设计令牌（Design Tokens）**：使用 CSS 自定义属性实现深色/浅色主题，所有颜色、间距、圆角均来自 Fluent UI 2 的 token 体系
- **组件规范**：TabList、Table、Card、Button、Switch、Toast、Tooltip、Divider 等组件的尺寸、间距、动画均参照 Fluent UI 2 React 组件的实测值
- **Fluent UI System Icons**：所有图标使用 `@fluentui/svg-icons` 内联 SVG，`fill="currentColor"` 跟随主题
- **间距体系**：所有间距为 4px 倍数（4/8/12/16/20/24px），遵循 Fluent UI 2 的 4px 网格系统
- **主题切换**：默认跟随 Windows 系统主题（`prefers-color-scheme`），也可手动切换

参考资料：
- [Fluent UI 2 Design](https://fluent2.microsoft.design/)
- [Fluent UI React v9](https://react.fluentui.dev/)
- [Fluent UI System Icons](https://github.com/microsoft/fluentui-system-icons)

## 目录结构

```
wuwa-gacha-analyzer/
├── wuwa_gacha.py         # 抽卡记录抓取
├── gacha_report.py       # 静态 HTML 报告生成
├── wuwa_server.py        # 本地 Web 服务
├── README.md             # 本文件
├── icons/                # 图标缓存（自动生成）
│   ├── characters/       # 角色头像
│   ├── weapons/          # 武器图标
│   └── encore_mapping.json  # 备用图标映射缓存
└── uploads/              # 上传数据（Web 服务自动创建）
```

## 常见问题

**Q：抓取数据时提示 API 返回错误？**
A：凭证有效期有限（约 1 小时），需要重新抓包获取新的凭证。

**Q：Web 服务启动后浏览器打不开？**
A：检查防火墙是否拦截了 8766 端口，或尝试 `--port` 换一个端口。

**Q：合并数据后记录变多了？**
A：合并采用"截断+追加"策略，新数据会覆盖旧数据中重叠的时间段，不会产生重复记录。

## 致谢

- 角色武器图标来源：[files.wuthery.com](https://files.wuthery.com)、[encore.moe](https://encore.moe)
- 保底概率模型参考：NGA 社区验证数据

## 许可

MIT License
